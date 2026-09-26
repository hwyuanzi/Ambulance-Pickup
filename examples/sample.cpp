// One-file C++ submission. Reads the 2023 input from standard input.
#include <iostream>
#include <sstream>
#include <string>

int main() {
    std::string line;
    int mode = 0, x = 0, y = 0, deadline = 0;
    bool have_patient = false;
    int hospitals = 0, first_available = 0;
    while (std::getline(std::cin, line)) {
        if (line.rfind("person", 0) == 0) { mode = 1; continue; }
        if (line.rfind("hospital", 0) == 0) { mode = 2; continue; }
        if (mode == 1 && !have_patient) {
            char comma1, comma2;
            std::istringstream input(line);
            if (input >> x >> comma1 >> y >> comma2 >> deadline && comma1 == ',' && comma2 == ',')
                have_patient = true;
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
    for (int i = 1; i <= hospitals; ++i) std::cout << "H" << i << ":" << x << "," << y << std::endl;
    if (first_available && deadline >= 2)
        std::cout << "0 H" << first_available << " P1 H" << first_available << std::endl;
}
