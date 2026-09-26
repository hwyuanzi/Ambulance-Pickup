/* One-file C submission. Reads the 2023 input from standard input. */
#include <stdio.h>
#include <string.h>

int main(void) {
    char line[256];
    int mode = 0, x = 0, y = 0, deadline = 0;
    int have_patient = 0, hospitals = 0, first_available = 0;
    while (fgets(line, sizeof line, stdin)) {
        if (strncmp(line, "person", 6) == 0) { mode = 1; continue; }
        if (strncmp(line, "hospital", 8) == 0) { mode = 2; continue; }
        if (mode == 1 && !have_patient && sscanf(line, "%d,%d,%d", &x, &y, &deadline) == 3) {
            have_patient = 1;
        } else if (mode == 2) {
            int count;
            if (sscanf(line, "%d", &count) == 1) {
                hospitals++;
                if (count > 0 && first_available == 0) first_available = hospitals;
            }
        }
    }
    if (!have_patient) return 0;
    for (int i = 1; i <= hospitals; i++) {
        printf("H%d:%d,%d\n", i, x, y);
        fflush(stdout);
    }
    if (first_available && deadline >= 2) {
        printf("0 H%d P1 H%d\n", first_available, first_available);
        fflush(stdout);
    }
    return 0;
}
