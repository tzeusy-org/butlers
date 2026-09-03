/* Adversarial fork/double-fork/setsid payload for task 3.6b's real-kernel
 * descendant-survival proof (bu-q6vjl).  Runs as namespace PID1 inside the
 * Dashboard CLI-auth Bubblewrap sandbox.  It detaches a grandchild via
 * setsid()+double-fork, then exits immediately so the harness can prove the
 * kernel's PID-namespace teardown (PID1 exit) reaps the detached descendant
 * before its delayed write can land -- not any process-group/setsid handling
 * this file itself performs.
 */
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <sys/wait.h>

static void write_marker(const char *path, const char *content) {
    int fd = open(path, O_WRONLY | O_CREAT | O_TRUNC, 0600);
    if (fd < 0) {
        _exit(90);
    }
    ssize_t length = (ssize_t)strlen(content);
    if (write(fd, content, (size_t)length) != length) {
        _exit(91);
    }
    close(fd);
}

int main(void) {
    pid_t first = fork();
    if (first < 0) {
        return 1;
    }
    if (first == 0) {
        if (setsid() < 0) {
            _exit(92);
        }
        pid_t second = fork();
        if (second < 0) {
            _exit(93);
        }
        if (second == 0) {
            /* Fully detached descendant: prove it started, then attempt a
             * delayed mutation the direct child's exit should preempt. */
            write_marker("/home/runtime/descendant-started.marker", "alive");
            struct timespec delay = {2, 0};
            nanosleep(&delay, NULL);
            write_marker("/home/runtime/descendant-survived.marker", "mutated-after-exit");
            _exit(0);
        }
        /* First-level child exits immediately, orphaning the grandchild. */
        _exit(0);
    }

    /* Namespace PID1 (the "direct child" the trusted parent observes): reap
     * the immediate child, announce readiness, then exit right away. */
    int status = 0;
    waitpid(first, &status, 0);
    printf("BUTLERS_DESCENDANT_SURVIVAL_READY\n");
    fflush(stdout);
    return 0;
}
