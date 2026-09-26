# One-file Julia submission. Reads the 2023 input from standard input.
mode = 0
x = 0
y = 0
deadline = 0
have_patient = false
hospitals = 0
first_available = 0

for raw in eachline(stdin)
    line = strip(raw)
    if startswith(line, "person")
        global mode = 1
    elseif startswith(line, "hospital")
        global mode = 2
    elseif mode == 1 && !have_patient
        fields = split(line, ',')
        if length(fields) == 3
            global x = parse(Int, fields[1])
            global y = parse(Int, fields[2])
            global deadline = parse(Int, fields[3])
            global have_patient = true
        end
    elseif mode == 2 && !isempty(line)
        count = parse(Int, line)
        global hospitals += 1
        if count > 0 && first_available == 0
            global first_available = hospitals
        end
    end
end

if have_patient
    for i in 1:hospitals
        println("H$i:$x,$y")
        flush(stdout)
    end
    if first_available > 0 && deadline >= 2
        println("0 H$first_available P1 H$first_available")
        flush(stdout)
    end
end
