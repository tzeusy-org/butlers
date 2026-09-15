/* Adversarial signer/protected-environment probe (bu-xj2gi).  Runs as
 * namespace PID1 inside the Dashboard CLI-auth Bubblewrap sandbox and
 * attempts to (1) open the real, absolute, root-owned mode-0400 signer path
 * and (2) recover the orchestrator's protected marker environment variable,
 * both via the ordinary libc syscalls a hostile provider CLI could use and
 * via a raw read of this process's own kernel-tracked environment block
 * (/proc/self/environ) rather than trusting getenv()'s bookkeeping alone.
 */
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define SIGNER_PATH "/run/secrets/runtime_probe_control_signing_key"
#define MARKER_ENV_NAME "BU_XJ2GI_PROTECTED_MARKER"

/* Returns 1 if the marker key is present in this process's real kernel
 * environment block, 0 if the block was read and it is absent, -1 if the
 * block could not be read at all. */
static int proc_environ_contains_marker(void) {
    FILE *stream = fopen("/proc/self/environ", "rb");
    if (stream == NULL) {
        return -1;
    }
    static char buffer[65536];
    size_t total = 0;
    size_t chunk;
    while (total < sizeof(buffer) - 1 &&
           (chunk = fread(buffer + total, 1, sizeof(buffer) - 1 - total, stream)) > 0) {
        total += chunk;
    }
    fclose(stream);
    buffer[total] = '\0';

    const char *needle = MARKER_ENV_NAME "=";
    size_t needle_len = strlen(needle);
    for (size_t i = 0; i + needle_len <= total; i++) {
        if (memcmp(buffer + i, needle, needle_len) == 0) {
            return 1;
        }
    }
    return 0;
}

int main(void) {
    int signer_fd = open(SIGNER_PATH, O_RDONLY);
    int signer_opened = signer_fd >= 0;
    int signer_errno = signer_opened ? 0 : errno;
    if (signer_opened) {
        close(signer_fd);
    }

    int marker_in_getenv = getenv(MARKER_ENV_NAME) != NULL;
    int marker_in_environ = proc_environ_contains_marker();

    printf(
        "{\"signer_opened\":%s,\"signer_errno\":%d,\"marker_in_getenv\":%s,"
        "\"marker_in_proc_environ\":%s}\n",
        signer_opened ? "true" : "false",
        signer_errno,
        marker_in_getenv ? "true" : "false",
        marker_in_environ < 0 ? "null" : (marker_in_environ ? "true" : "false"));
    fflush(stdout);
    return 0;
}
