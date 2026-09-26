// One-file C++ submission. Reads the instance from standard input.
#include <iostream>
#include <sstream>
#include <string>

int main() {
    std::string line;
    int mode = 0, x = 0, y = 0, deadline = 0, max_x = 0;
    bool have_patient = false;
    int hospitals = 0, first_available = 0;
    while (std::getline(std::cin, line)) {
        if (line.rfind("person", 0) == 0) { mode = 1; continue; }
        if (line.rfind("hospital", 0) == 0) { mode = 2; continue; }
        if (mode == 1) {
            int px, py, pd;
            char comma1, comma2;
            std::istringstream input(line);
            if (input >> px >> comma1 >> py >> comma2 >> pd && comma1 == ',' && comma2 == ',') {
                if (!have_patient) { x = px; y = py; deadline = pd; max_x = px; have_patient = true; }
                if (px > max_x) max_x = px;
            }
        } else if (mode == 2) {
            int count;
            std::istringstream input(line);
            if (input >> count) {
                ++hospitals;
                if (count > 0 && first_available == 0) first_available = hospitals;
            }
        }
    }
    if (!have_patient) return 0;
    int hospital_x = max_x + 1;
    for (int i = 1; i <= hospitals; ++i) std::cout << "H" << i << ":" << hospital_x << "," << y << std::endl;
    if (first_available && 2 * (hospital_x - x) + 2 <= deadline)
        std::cout << "0 H" << first_available << " P1 H" << first_available << std::endl;
}
