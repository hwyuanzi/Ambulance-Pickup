# One-file Julia submission. Reads the instance from standard input.
mode = 0
x = 0
y = 0
deadline = 0
max_x = 0
have_patient = false
hospitals = 0
first_available = 0

for raw in eachline(stdin)
    line = strip(raw)
    if startswith(line, "person")
        global mode = 1
    elseif startswith(line, "hospital")
        global mode = 2
    elseif mode == 1
        fields = split(line, ',')
        if length(fields) == 3
            px = parse(Int, fields[1])
            if !have_patient
                global x = px
                global y = parse(Int, fields[2])
                global deadline = parse(Int, fields[3])
                global max_x = px
                global have_patient = true
            end
            global max_x = max(max_x, px)
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
    hospital_x = max_x + 1
    for i in 1:hospitals
        println("H$i:$hospital_x,$y")
        flush(stdout)
    end
    if first_available > 0 && 2 * (hospital_x - x) + 2 <= deadline
        println("0 H$first_available P1 H$first_available")
        flush(stdout)
    end
end
