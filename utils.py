from Infra.Person import Person
from Infra.Hospital import Hospital

# read_data
def read_data(fname="data.txt", print_input_file=False):
    persons = []
    hospitals = []
    mode = 0
    pid = 1
    hid = 0
    with open(fname, 'r') as fil:
        data = fil.readlines()

    for line in data:
        line = line.strip().lower()
        if line.startswith("person") or line.startswith("people"):
            mode = 1
        elif line.startswith("hospital"):
            mode = 2
        elif line:
            if mode == 1:
                (a, b, c) = map(int, line.split(","))
                persons.append(Person(pid,a, b, c))
                pid += 1
            elif mode == 2:
                (c,) = map(int, line.split(","))
                hospitals.append(Hospital(hid, -1, -1, c))
                hid += 1

    if print_input_file:
        print('Reading data:', fname)
        print(persons)
        print(hospitals)
    return persons, hospitals