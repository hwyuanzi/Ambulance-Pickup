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

    for line_number, raw in enumerate(data, 1):
        line = raw.strip().lower()
        if not line:
            continue
        if line.startswith("person") or line.startswith("people"):
            if mode != 0:
                raise ValueError(f"Line {line_number}: duplicate or out-of-order patient header")
            mode = 1
        elif line.startswith("hospital"):
            if mode != 1 or not persons:
                raise ValueError(f"Line {line_number}: hospital header must follow patient rows")
            mode = 2
        else:
            try:
                if mode == 1:
                    (a, b, c) = map(int, line.split(","))
                    if c < 0:
                        raise ValueError("deadline must be nonnegative")
                    persons.append(Person(pid, a, b, c))
                    pid += 1
                elif mode == 2:
                    (c,) = map(int, line.split(","))
                    if c < 0:
                        raise ValueError("ambulance count must be nonnegative")
                    hospitals.append(Hospital(hid, -1, -1, c))
                    hid += 1
                else:
                    raise ValueError("patient header required before data")
            except ValueError as exc:
                raise ValueError(f"Line {line_number}: invalid instance data: {exc}") from exc

    if not persons or not hospitals:
        raise ValueError("Instance needs at least one patient and one hospital")

    if print_input_file:
        print('Reading data:', fname)
        print(persons)
        print(hospitals)
    return persons, hospitals
