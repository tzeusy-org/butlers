// whatsapp-bridge: Go sidecar binary wrapping whatsmeow for WhatsApp Web multidevice protocol.
//
// Usage:
//
//	whatsapp-bridge --db-dsn <dsn> --listen unix:///tmp/wa-bridge.sock   # run (default)
//	whatsapp-bridge pair --db-dsn <dsn>                                   # interactive QR pairing
//	whatsapp-bridge status --db-dsn <dsn>                                 # print session state
package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"log"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/store/sqlstore"
	"go.mau.fi/whatsmeow/types"
	waEvents "go.mau.fi/whatsmeow/types/events"
	waLog "go.mau.fi/whatsmeow/util/log"
	"google.golang.org/protobuf/proto"

	"github.com/skip2/go-qrcode"

	"github.com/butlers/whatsapp-bridge/internal/api"
	bridgeEvents "github.com/butlers/whatsapp-bridge/internal/events"
	"github.com/butlers/whatsapp-bridge/internal/store"

	// PostgreSQL driver
	_ "github.com/lib/pq"

	waE2E "go.mau.fi/whatsmeow/proto/waE2E"
)

const (
	pairingTimeout = 120 * time.Second
	exitOK         = 0
	exitTimeout    = 1
	exitInvalidated = 2
)

func main() {
	if len(os.Args) > 1 && !strings.HasPrefix(os.Args[1], "-") {
		switch os.Args[1] {
		case "pair":
			runPair(os.Args[2:])
			return
		case "status":
			runStatus(os.Args[2:])
			return
		case "help", "--help", "-h":
			printUsage()
			os.Exit(exitOK)
		}
	}
	runBridge(os.Args[1:])
}

func printUsage() {
	fmt.Fprintln(os.Stderr, "Usage:")
	fmt.Fprintln(os.Stderr, "  whatsapp-bridge [--db-dsn DSN] [--listen unix:///tmp/wa-bridge.sock]")
	fmt.Fprintln(os.Stderr, "  whatsapp-bridge pair --db-dsn DSN")
	fmt.Fprintln(os.Stderr, "  whatsapp-bridge status --db-dsn DSN")
}

// ------------------------------------------------------------------
// run (default): connect using stored session, serve HTTP API
// ------------------------------------------------------------------

func runBridge(args []string) {
	fs := flag.NewFlagSet("bridge", flag.ExitOnError)
	dbDSN := fs.String("db-dsn", envOrDefault("WA_DB_DSN", ""), "PostgreSQL DSN")
	listenAddr := fs.String("listen", envOrDefault("WA_LISTEN", "unix:///tmp/wa-bridge.sock"), "Listen address (unix:// or tcp://)")
	_ = fs.Parse(args)

	// exitCode is set by event handlers before cancelling context, so that
	// deferred cleanup runs before os.Exit is called.
	exitCode := exitOK

	if *dbDSN == "" {
		log.Fatal("--db-dsn is required")
	}

	socketPath := parseSocketPath(*listenAddr)

	// Open session store.
	sess, err := store.New(*dbDSN)
	if err != nil {
		log.Fatalf("open session store: %v", err)
	}
	defer sess.Close()

	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()

	// Load whatsmeow device store from PostgreSQL.
	container, err := sqlstore.New(ctx, "postgres", *dbDSN, waLog.Stdout("whatsmeow-db", "INFO", false))
	if err != nil {
		log.Fatalf("open whatsmeow sqlstore: %v", err)
	}

	deviceStore, err := container.GetFirstDevice(ctx)
	if err != nil {
		log.Fatalf("get device store: %v", err)
	}

	client := whatsmeow.NewClient(deviceStore, waLog.Stdout("whatsmeow", "INFO", false))
	client.EnableAutoReconnect = true

	// Prepare API server.
	srv := api.NewServer(socketPath, cancel)

	if err := srv.Start(ctx); err != nil {
		log.Fatalf("start API server: %v", err)
	}
	defer func() {
		stopCtx, stopCancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer stopCancel()
		srv.Stop(stopCtx)
	}()

	// Wire send function.
	srv.SetSendFn(func(sendCtx context.Context, recipient, text, replyTo string) (string, int64, error) {
		jid, err := parseJID(recipient)
		if err != nil {
			return "", 0, fmt.Errorf("invalid JID %q: %w", recipient, err)
		}
		msg := &waE2E.Message{Conversation: proto.String(text)}
		if replyTo != "" {
			msg = &waE2E.Message{
				ExtendedTextMessage: &waE2E.ExtendedTextMessage{
					Text: proto.String(text),
					ContextInfo: &waE2E.ContextInfo{
						StanzaID: proto.String(replyTo),
					},
				},
			}
		}
		resp, err := client.SendMessage(sendCtx, jid, msg)
		if err != nil {
			return "", 0, err
		}
		return resp.ID, resp.Timestamp.Unix(), nil
	})

	// Register event handler.
	client.AddEventHandler(func(rawEvt any) {
		switch evt := rawEvt.(type) {
		case *waEvents.Connected:
			phone := ""
			if client.Store.ID != nil {
				phone = client.Store.ID.User
			}
			srv.SetState(api.StateConnected, phone)
			// Touch last_seen_at for the active session.
			if activeSess, sErr := sess.GetAnyActive(ctx); sErr == nil {
				_ = sess.TouchLastSeen(ctx, activeSess.ID)
			}
			// If pairing just completed, notify the API server.
			if phone != "" {
				srv.NotifyPaired(phone)
			}
			log.Printf("connected: phone=%s", phone)

		case *waEvents.Disconnected:
			srv.SetState(api.StateDisconnected, "")
			log.Printf("disconnected from WhatsApp")

		case *waEvents.LoggedOut:
			log.Printf("logged out (reason=%v), marking session invalid and exiting", evt.Reason)
			phone := ""
			if client.Store.ID != nil {
				phone = client.Store.ID.User
			}
			// Mark session inactive.
			activeSess, sErr := sess.GetAnyActive(ctx)
			if sErr == nil {
				_ = sess.MarkInactive(ctx, activeSess.ID)
			}
			// Emit session_invalidated event synchronously before shutdown.
			srv.PublishEvent(bridgeEvents.MapSessionInvalidated(phone))
			// Signal shutdown with the invalidated exit code; defers handle cleanup.
			exitCode = exitInvalidated
			cancel()

		case *waEvents.PairSuccess:
			phone := evt.ID.User
			log.Printf("pair success: phone=%s", phone)
			// Save session to whatsapp_sessions.
			deviceID := evt.ID.String()
			sessionData, _ := json.Marshal(map[string]string{
				"jid":      deviceID,
				"platform": "whatsapp-bridge",
			})
			if _, sErr := sess.SaveNew(ctx, phone, deviceID, sessionData); sErr != nil {
				log.Printf("warning: failed to write whatsapp_sessions row after pair: %v", sErr)
			}
			srv.NotifyPaired(phone)

		case *waEvents.Message:
			be := bridgeEvents.MapMessage(evt)
			if be != nil {
				srv.PublishEvent(be)
			}
		}
	})

	// If no device is paired yet, start QR channel mode for the HTTP pair/start flow.
	if deviceStore.ID == nil {
		log.Printf("no paired device found — waiting for HTTP /pair/start")
		srv.SetState(api.StatePairRequired, "")

		qrChan, err := client.GetQRChannel(ctx)
		if err != nil {
			log.Fatalf("get QR channel: %v", err)
		}

		if err := client.Connect(); err != nil {
			log.Fatalf("connect for pairing: %v", err)
		}

		// Process QR codes in the background and feed them to the API server.
		go func() {
			for item := range qrChan {
				switch item.Event {
				case whatsmeow.QRChannelEventCode:
					expiry := time.Now().Add(item.Timeout)
					srv.SetQRCode(item.Code, expiry)
					log.Printf("new QR code available (expires in %s)", item.Timeout.Round(time.Second))
				case whatsmeow.QRChannelSuccess.Event:
					// NotifyPaired is handled by the Connected/PairSuccess event handler.
					log.Printf("QR pairing successful")
				case whatsmeow.QRChannelTimeout.Event:
					srv.NotifyPairExpired()
					log.Printf("QR pairing timed out")
				default:
					log.Printf("QR channel event: %s (error: %v)", item.Event, item.Error)
				}
			}
		}()
	} else {
		// Connect using stored session.
		srv.SetState(api.StateConnecting, "")
		if err := client.Connect(); err != nil {
			log.Fatalf("connect: %v", err)
		}
	}

	<-ctx.Done()
	log.Println("shutting down bridge")
	client.Disconnect()
	os.Exit(exitCode)
}

// ------------------------------------------------------------------
// pair: interactive QR pairing mode
// ------------------------------------------------------------------

func runPair(args []string) {
	fs := flag.NewFlagSet("pair", flag.ExitOnError)
	dbDSN := fs.String("db-dsn", envOrDefault("WA_DB_DSN", ""), "PostgreSQL DSN")
	_ = fs.Parse(args)

	if *dbDSN == "" {
		log.Fatal("--db-dsn is required")
	}

	ctx, cancel := context.WithTimeout(context.Background(), pairingTimeout)
	defer cancel()

	// Load whatsmeow device store from PostgreSQL.
	container, err := sqlstore.New(ctx, "postgres", *dbDSN, waLog.Stdout("whatsmeow-db", "INFO", false))
	if err != nil {
		log.Fatalf("open whatsmeow sqlstore: %v", err)
	}

	deviceStore, err := container.GetFirstDevice(ctx)
	if err != nil {
		log.Fatalf("get device store: %v", err)
	}

	// If there's already a paired device, clear it so we can re-pair.
	if deviceStore.ID != nil {
		log.Printf("clearing existing device store for re-pair")
		if err := container.DeleteDevice(ctx, deviceStore); err != nil {
			log.Fatalf("delete device: %v", err)
		}
		// Re-fetch a fresh (empty) device store.
		deviceStore, err = container.GetFirstDevice(ctx)
		if err != nil {
			log.Fatalf("get fresh device store: %v", err)
		}
	}

	client := whatsmeow.NewClient(deviceStore, waLog.Stdout("whatsmeow", "INFO", false))

	qrChan, err := client.GetQRChannel(ctx)
	if err != nil {
		log.Fatalf("get QR channel: %v", err)
	}

	if err := client.Connect(); err != nil {
		log.Fatalf("connect: %v", err)
	}
	defer client.Disconnect()

	for item := range qrChan {
		switch item.Event {
		case whatsmeow.QRChannelEventCode:
			// Render QR code to terminal.
			qr, err := qrcode.New(item.Code, qrcode.Medium)
			if err != nil {
				log.Printf("QR encode error: %v", err)
				continue
			}
			fmt.Print("\033[2J\033[H") // clear terminal
			fmt.Println("Scan this QR code with your WhatsApp mobile app:")
			fmt.Println(qr.ToSmallString(false))
			fmt.Printf("(expires in ~%s)\n", item.Timeout.Round(time.Second))

		case whatsmeow.QRChannelSuccess.Event:
			phone := ""
			if client.Store.ID != nil {
				phone = client.Store.ID.User
			}
			log.Printf("pairing successful: phone=%s", phone)

			// Persist session data to whatsapp_sessions table.
			sess, sErr := store.New(*dbDSN)
			if sErr != nil {
				log.Fatalf("open session store after pairing: %v", sErr)
			}
			deviceID := ""
			if client.Store.ID != nil {
				deviceID = client.Store.ID.String()
			}
			sessionData, _ := json.Marshal(map[string]string{
				"jid":      deviceID,
				"platform": "whatsapp-bridge",
			})
			if _, err := sess.SaveNew(ctx, phone, deviceID, sessionData); err != nil {
				log.Printf("warning: failed to write whatsapp_sessions row: %v", err)
			}
			sess.Close()
			os.Exit(exitOK)

		case whatsmeow.QRChannelTimeout.Event:
			fmt.Fprintln(os.Stderr, "Pairing timed out. Run 'whatsapp-bridge pair' again.")
			os.Exit(exitTimeout)

		default:
			log.Printf("QR channel event: %s (error: %v)", item.Event, item.Error)
			if item.Error != nil {
				fmt.Fprintf(os.Stderr, "Pairing error: %v\n", item.Error)
				os.Exit(exitTimeout)
			}
		}
	}

	// Channel closed without success — treat as timeout.
	fmt.Fprintln(os.Stderr, "Pairing timed out. Run 'whatsapp-bridge pair' again.")
	os.Exit(exitTimeout)
}

// ------------------------------------------------------------------
// status: print current session state
// ------------------------------------------------------------------

func runStatus(args []string) {
	fs := flag.NewFlagSet("status", flag.ExitOnError)
	dbDSN := fs.String("db-dsn", envOrDefault("WA_DB_DSN", ""), "PostgreSQL DSN")
	_ = fs.Parse(args)

	if *dbDSN == "" {
		log.Fatal("--db-dsn is required")
	}

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	sess, err := store.New(*dbDSN)
	if err != nil {
		log.Fatalf("open session store: %v", err)
	}
	defer sess.Close()

	sessions, err := sess.GetStatus(ctx)
	if err != nil {
		log.Fatalf("get status: %v", err)
	}

	if len(sessions) == 0 {
		fmt.Println("No sessions found. Run 'whatsapp-bridge pair' to pair a device.")
		os.Exit(exitOK)
	}

	fmt.Printf("%-36s  %-18s  %-6s  %-20s  %s\n", "ID", "PHONE", "ACTIVE", "PAIRED_AT", "LAST_SEEN_AT")
	for _, s := range sessions {
		fmt.Printf("%-36s  %-18s  %-6v  %-20s  %s\n",
			s.ID,
			s.PhoneNumber,
			s.Active,
			s.PairedAt.Format(time.RFC3339),
			s.LastSeenAt.Format(time.RFC3339),
		)
	}
	os.Exit(exitOK)
}

// ------------------------------------------------------------------
// Helpers
// ------------------------------------------------------------------

func parseSocketPath(addr string) string {
	switch {
	case strings.HasPrefix(addr, "unix://"):
		return strings.TrimPrefix(addr, "unix://")
	case strings.HasPrefix(addr, "unix:"):
		return strings.TrimPrefix(addr, "unix:")
	default:
		return addr
	}
}

func parseJID(s string) (types.JID, error) {
	if !strings.Contains(s, "@") {
		// Bare phone number — assume individual chat.
		s = s + "@s.whatsapp.net"
	}
	jid, err := types.ParseJID(s)
	if err != nil {
		return types.EmptyJID, err
	}
	return jid, nil
}

func envOrDefault(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}
