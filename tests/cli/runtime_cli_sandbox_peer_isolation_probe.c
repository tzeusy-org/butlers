/*
 * Adversarial peer-isolation probe for two concurrent Dashboard CLI-auth
 * Bubblewrap sandboxes (REQ-core-credentials-002, task 3.6b).
 *
 * This binary is never shipped in the production image. The test harness
 * compiles it on the host and bind-mounts it read-only into both the outer
 * container and, via the same launch-plan machinery production uses for a
 * provider binary, into each sandboxed child's empty root.
 *
 * Modes:
 *   victim   <seconds>                 -- sleep so a peer PID stays live.
 *   attacker <peer-stage-path> <peer-pid>
 *       Attempts, from inside its own sandbox, to read and write a file in
 *       the peer's staged HOME by its exact real path, to list that stage
 *       directory, to signal-probe the peer's real outer PID, and to find
 *       that PID in its own /proc view. Every attempt is expected to fail
 *       because the peer's stage was never bound into this sandbox and the
 *       peer's PID lives in a different PID namespace. Results are printed
 *       as one JSON line; this probe always exits 0 so the harness can
 *       inspect every field rather than stopping at the first failure.
 */

#define _GNU_SOURCE

#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static int is_all_digits(const char *name) {
    if (*name == '\0') {
        return 0;
    }
    for (const char *cursor = name; *cursor != '\0'; cursor++) {
        if (*cursor < '0' || *cursor > '9') {
            return 0;
        }
    }
    return 1;
}

static void run_victim(int argc, char *argv[]) {
    if (argc < 3) {
        _exit(2);
    }
    long seconds = strtol(argv[2], NULL, 10);
    if (seconds <= 0) {
        seconds = 1;
    }
    sleep((unsigned int)seconds);
    _exit(0);
}

static void run_attacker(int argc, char *argv[]) {
    if (argc < 4) {
        _exit(2);
    }
    const char *peer_stage = argv[2];
    const char *peer_pid_str = argv[3];
    pid_t peer_pid = (pid_t)strtol(peer_pid_str, NULL, 10);

    char secret_path[4096];
    char write_path[4096];
    if (
        snprintf(secret_path, sizeof(secret_path), "%s/victim-secret.txt", peer_stage)
            >= (int)sizeof(secret_path)
        || snprintf(write_path, sizeof(write_path), "%s/attacker-write.txt", peer_stage)
            >= (int)sizeof(write_path)
    ) {
        _exit(3);
    }

    int read_fd = open(secret_path, O_RDONLY);
    int read_errno = errno;
    if (read_fd >= 0) {
        close(read_fd);
    }

    int write_fd = open(write_path, O_WRONLY | O_CREAT | O_EXCL, 0644);
    int write_errno = errno;
    if (write_fd >= 0) {
        close(write_fd);
    }

    DIR *stage_dir = opendir(peer_stage);
    int opendir_errno = errno;
    if (stage_dir != NULL) {
        closedir(stage_dir);
    }

    int kill_result = kill(peer_pid, 0);
    int kill_errno = errno;

    int peer_pid_in_proc = 0;
    long proc_numeric_entries = 0;
    DIR *proc_dir = opendir("/proc");
    if (proc_dir != NULL) {
        struct dirent *entry;
        while ((entry = readdir(proc_dir)) != NULL) {
            if (!is_all_digits(entry->d_name)) {
                continue;
            }
            proc_numeric_entries++;
            if (strcmp(entry->d_name, peer_pid_str) == 0) {
                peer_pid_in_proc = 1;
            }
        }
        closedir(proc_dir);
    }

    printf(
        "{"
        "\"mode\":\"attacker\","
        "\"read_stage_secret_ok\":%s,\"read_stage_secret_errno\":%d,"
        "\"write_stage_ok\":%s,\"write_stage_errno\":%d,"
        "\"opendir_stage_ok\":%s,\"opendir_stage_errno\":%d,"
        "\"kill_peer_ok\":%s,\"kill_peer_errno\":%d,"
        "\"peer_pid_in_proc\":%s,\"proc_numeric_entries\":%ld"
        "}\n",
        read_fd >= 0 ? "true" : "false", read_errno,
        write_fd >= 0 ? "true" : "false", write_errno,
        stage_dir != NULL ? "true" : "false", opendir_errno,
        kill_result == 0 ? "true" : "false", kill_errno,
        peer_pid_in_proc ? "true" : "false", proc_numeric_entries
    );
    fflush(stdout);
    _exit(0);
}

int main(int argc, char *argv[]) {
    if (argc < 2) {
        _exit(2);
    }
    if (strcmp(argv[1], "victim") == 0) {
        run_victim(argc, argv);
    } else if (strcmp(argv[1], "attacker") == 0) {
        run_attacker(argc, argv);
    }
    _exit(2);
}
