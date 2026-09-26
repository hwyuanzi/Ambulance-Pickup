/* One-file C submission. Reads the instance from standard input. */
#include <stdio.h>
#include <string.h>

int main(void) {
    char line[256];
    int mode = 0, x = 0, y = 0, deadline = 0, max_x = 0;
    int have_patient = 0, hospitals = 0, first_available = 0;
    while (fgets(line, sizeof line, stdin)) {
        if (strncmp(line, "person", 6) == 0) { mode = 1; continue; }
        if (strncmp(line, "hospital", 8) == 0) { mode = 2; continue; }
        int px, py, pd;
        if (mode == 1 && sscanf(line, "%d,%d,%d", &px, &py, &pd) == 3) {
            if (!have_patient) { x = px; y = py; deadline = pd; max_x = px; have_patient = 1; }
            if (px > max_x) max_x = px;
        } else if (mode == 2) {
            int count;
            if (sscanf(line, "%d", &count) == 1) {
                hospitals++;
                if (count > 0 && first_available == 0) first_available = hospitals;
            }
        }
    }
    if (!have_patient) return 0;
    int hospital_x = max_x + 1;
    for (int i = 1; i <= hospitals; i++) {
        printf("H%d:%d,%d\n", i, hospital_x, y);
        fflush(stdout);
    }
    if (first_available && 2 * (hospital_x - x) + 2 <= deadline) {
        printf("0 H%d P1 H%d\n", first_available, first_available);
        fflush(stdout);
    }
    return 0;
}
