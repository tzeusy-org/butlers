/*
 * Namespace PID 1 for a Dashboard CLI-auth Bubblewrap invocation.
 *
 * Bubblewrap setup descriptors must reach this shim for the startup
 * handshake, but provider code must never inherit them.  Python cannot rely
 * on os.close_range() on every supported image, so this deliberately small C
 * executable calls the kernel syscall directly, verifies the result using the
 * fresh sandbox procfs, acknowledges on stdout, then execve()s the approved
 * provider command.
 */

#define _GNU_SOURCE

#include <dirent.h>
#include <errno.h>
#include <limits.h>
#include <stdlib.h>
#include <string.h>
#include <sys/syscall.h>
#include <unistd.h>

static int write_all(int fd, const char *buffer, size_t length) {
    size_t written = 0;

    while (written < length) {
        ssize_t result = write(fd, buffer + written, length - written);
        if (result < 0) {
            if (errno == EINTR) {
                continue;
            }
            return -1;
        }
        written += (size_t)result;
    }
    return 0;
}

static int close_payload_descriptors(void) {
#ifdef SYS_close_range
    if (syscall(SYS_close_range, 3u, UINT_MAX, 0u) != 0) {
        return -1;
    }
#else
    errno = ENOSYS;
    return -1;
#endif

    DIR *directory = opendir("/proc/self/fd");
    if (directory == NULL) {
        return -1;
    }
    int directory_fd = dirfd(directory);
    struct dirent *entry = NULL;
    while ((entry = readdir(directory)) != NULL) {
        char *end = NULL;
        errno = 0;
        long descriptor = strtol(entry->d_name, &end, 10);
        if (errno != 0 || end == entry->d_name || *end != '\0') {
            continue;
        }
        if (descriptor > STDERR_FILENO && descriptor != directory_fd) {
            (void)closedir(directory);
            errno = EBADF;
            return -1;
        }
    }
    return closedir(directory);
}

int main(int argc, char *argv[]) {
    static const char ready[] = "BUTLERS_RUNTIME_CLI_SANDBOX_READY\n";
    static const char failure[] = "runtime-cli-sandbox-init failed\n";

    if (argc < 3 || strcmp(argv[1], "--") != 0) {
        (void)write_all(STDERR_FILENO, failure, sizeof(failure) - 1);
        return 125;
    }
    if (close_payload_descriptors() != 0) {
        (void)write_all(STDERR_FILENO, failure, sizeof(failure) - 1);
        return 125;
    }
    if (write_all(STDOUT_FILENO, ready, sizeof(ready) - 1) != 0) {
        return 125;
    }

    execv(argv[2], &argv[2]);
    (void)write_all(STDERR_FILENO, failure, sizeof(failure) - 1);
    return 126;
}
