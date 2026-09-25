import utils 

def solution():
    input_file = utils.read_data("input_data.txt", False)
    lines = []
    for i in range(1, 6):
        lines.append("H" + str(i) + ":" + str(i * 10) + "," + str(i * 10))
    lines.append("")
    patient = 1
    for i in range(1, 40):
        pickups = 1
        hospital = "H" + str((i % 5) + 1)
        patients = ["P" + str(patient + z) for z in range(pickups)] 
        patient += pickups
        patient_string = " ".join(patients)
        line = "0 " + hospital + " " + patient_string + " " + hospital
        lines.append(line)
    #print to output file
    return "\n".join(lines)

if __name__ == "__main__":
    result = solution()
    print(result)