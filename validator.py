#!/usr/bin/env python

import re
import pandas as pd
import sys
from utils import read_data
from Infra.exceptions import IllegalPlanError, FormatSyntaxError, ValidationError
from Infra.Person import Person
from Infra.Hospital import Hospital

import matplotlib.pyplot as plt
from matplotlib.ticker import (AutoMinorLocator, MultipleLocator)


def read_hospital(line, hospitals, placed_hospitals):
    print("\n" + line, end=" ")
    hospital_no, hospital_coordinates = line.strip().replace('H', '').split(':')
    hospital_no = int(hospital_no)
    if hospital_no > len(hospitals):
        raise ValidationError("Invalid Hospital Number - hospital number should be from 1 to " + str(len(hospitals)) + " line: "  + line)
    elif hospital_no in placed_hospitals:
        raise ValidationError("Hospital Number " + str(hospital_no) + " already placed with coordinates " + str(hospitals[hospital_no - 1].x) + "," + str(hospitals[hospital_no - 1].y) + " line: " + line)
    placed_hospitals.add(hospital_no)
    hospital_coordinates = hospital_coordinates.split(',')
    try:
        (x, y) = [int(coordinate) for coordinate in hospital_coordinates]
    except ValueError:
        raise ValidationError("Invalid Hospital Coordinates: " + line)
    print("Hospital #{idx}: coordinates ({x},{y})".format(x=x, y=y, idx=hospital_no))
    hospitals[hospital_no - 1].x = x
    hospitals[hospital_no - 1].y = y
    
# read_results
def readresults(persons, hospitals, fname='Outputs/sample_result.txt'):
    print('Reading data:', fname)
    res = {}
    score = 0
    with open(fname, 'r') as fil:
        data = fil.readlines()
    placed_hospitals = set()
    for (i, line) in enumerate(data):
        # check for hospital coordinates
        if line.startswith('H'):
            read_hospital(line, hospitals, placed_hospitals)
            data[i] = '0 .' + line
        else:
            line = line.strip()
            if not line:
                data[i] = '0 .' + line


    data.sort(key = lambda x: int(x.split()[0]))
    for line in data:
        line = line.strip()
        if not line or line.startswith('0 .'):
            continue

        print("\n" + line, end="\n ")

        # check for ambulance records
        try:
            hos = None
            end_hos = None
            rescue_persons = []
            start_time = int(line.split()[0])
            route = line.split()[1:]

            for (i, w) in enumerate(route):
                # Hospital 
                if w[0] == 'H':
                    hospital_no = int(w[1:])
                    if hospital_no <= 0 or len(hospitals) < hospital_no:
                        raise FormatSyntaxError('Illegal hospital id: %d' % hospital_no)
                    if i!=0 and i!=len(route)-1:
                        raise FormatSyntaxError('Specify hospitals only at beginning or at end of route: %r' % line)
                    if i == 0:
                        hos = hospitals[hospital_no - 1]
                    else:
                        end_hos = hospitals[hospital_no - 1]
                    continue

                # Person
                if w[0] == 'P':
                    if i==0 or i==len(route)-1:
                        raise FormatSyntaxError('Only specify hospitals at beginning or at end of route: %r' % line)
                    person_index = int(w[1:])
                    if person_index <= 0 or len(persons) < person_index:
                        raise FormatSyntaxError('Illegal person id: %d' % person_index)
                    per = persons[person_index - 1]
                    rescue_persons.append(per)
                    continue
                # error
                raise FormatSyntaxError('!!! Incorrect format: %r' % line)

            if not hos or not rescue_persons:
                print('!!! Insufficient data: %r' % line)
                continue
            if not hos or not end_hos:
                raise FormatSyntaxError('Either start hospital or end hospital is not defined.')
            rescue_persons = hos.rescue(rescue_persons, end_hos, start_time)
            if hos.hid in res:
                res[hos.hid].extend(rescue_persons)
            else:
                res[hos.hid] = rescue_persons
            score += len(rescue_persons)
        except ValidationError as x:
            print('!!!', x)

        # uncomment the following lines to see the hospital-ambulance status after every ambulance tour line            
        # for hos in hospitals: 
        #     print("Hospital ", hos.hid, "::: ", end = " ") 
        #     for t in hos.amb_time: 
        #         print(t, end = " ") 
        #     print()
        # print("___________________________\n") 

    print('\nTotal score:', score, '\n')
    return res


def my_solution(pers, hosps):
    """
    Place your custom solution here
    Your code SHOULD create a result.txt file consisting of the solution as per the output format
    As such, you do not need to read the data and can use the read_data() function directly.
    However, you're free to read the data if you wish to use a separate data structure
    IO Format:
        Refer to the Readme: "For Languages other than python" to know the input and output formats
    :return: Create a file named "result.txt" following the output format
    """

    # Enter your logic here

    # with open("outputs/team_name.txt", "w+") as fil:
    #     op = ""  # Change this to your solution
    #     fil.write(op)
    return


# Main
if __name__ == "__main__":
    input_file = "input_data.txt"
    result_file = "Test/sample_result.txt" #Change here for custom validation of your solution
    if len(sys.argv) > 1:
        input_file = sys.argv[1]
    if len(sys.argv) > 2:
        result_file = sys.argv[2]
    print("Using " + input_file + " as input.\n")
    (persons, hospitals) = read_data(input_file)
    my_solution(persons, hospitals)
    readresults(persons, hospitals, result_file)
