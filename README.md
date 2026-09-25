
# Ambulance Pickup Problem

## Description:

The ambulance planning real-time problem is to rescue as many people as possible following a disaster. The problem statement identifies the locations of people and the time they have to live. You can also establish mobile hospitals at the beginning of the problem. The problem is to get as many people to the hospitals on time as possible.

In our case, the graph is the Manhattan grid with every street going both ways. It takes a minute to go one block either north-south or east-west. Each hospital has an (x,y) location that you can determine when you see the distribution of victims. The ambulances need not return to the hospital where they begin. Each ambulance can carry up to four people. It takes one minute to load a person and one minute to unload up to four people. Each person will have a rescue time which is the number of minutes from now when the person should be unloaded in the hospital to survive. By the way, this problem is very similar to the vehicle routing problem about which there is an enormous literature and nice code like "jsprit" which was used in 2015 to great effect.

The data will be in the form:
```angular2html
person(xloc, yloc, rescuetime)
hospital(numambulance)
```

## Contact:

```angular2html
Mingyi Lim
Email: ml9027@nyu.edu
```

## Notes:

   1. You are responsible to check for the order in which the ambulances are departed. 
i.e. if A has 3 ambulances, there is a path B -> A making 4 total at A, 
you will need to print the result B -> A before departing the 4th ambulance from A.
   2. If you run into any format issues, the program will only leave an error output, but NOT stop the execution.
Even if your output is being validated, it may have some errors printed (Sample output has few such errors displayed)
   3. If you find any bugs in the code, feel free to let us know, and we'll fix it for you.
   4. Kindly make your submissions (even if a skeleton with proper but non-optimized output) by end of Thursday (Oct 5, 2023), 17:10 
This gives us a buffer to get back to you incase there is any error in compiling your code.

## Usage (Python):

Place your logic in `my_solution()` on line 277 and the code should do the rest for you.

If you do not want to mess with the validator file, you are free to follow the approach below (for other languages)
and you should be fine with using another file.

NOTE: The `read_data()` returns the list of **objects** and not a dictionary. 
You can use `object.prettify()` to get the dictionary instead, or add another function as per your requirements.

## For Languages other than python:

**Input:** The data will be in the `input_data.txt` file.

The given input format is:

```angular2html
person(xloc,yloc,rescuetime)
1,1,10
...
...
2,2,20

hospital(numambulance)
1
2
3
```

### Explanation of Input format

#### People

The first n lines of the input file will contain a list of people which are placed across the grid. 
A line of 

```103,51,20``` 

denotes that there is a person at coordinates ```(103, 51)``` who will expire by minute ```20```. For avoidance of doubt, the person must arrive at a hospital at or before minute 20 to survive.

Each person is 1-indexed in the order that they are listed in the input file.

Given: 
```
person(xloc,yloc,rescuetime)
55,300,122
105,45,110
45,157,117
```

* ```P1``` will refer to the person at (55, 300)
* ```P2``` will refer to the person at (105,45)
* ```P3``` will refer to the person at (45, 157)

This will be used later in the output file.

#### Hospitals
Each line in hospitals contains a single number. This is the number of ambulances the hospital will start with.
So given: 
   ``` 
   hospital(numambulance)
   5
   11
   7
   7
   ```
   * H1 will correspond to the first hospital with 5 ambulances, 
   * H2 will correspond to the second hospital with 11 ambulances
   * H3 will correspond to the third hospital with 7 ambulances.
   * H4 will correspond to the fourth hospital with 7 ambulances.

This will also be used later in the output file.

**Output:** Once you calculate your results, generate a `[team_name].txt` file under `Outputs` folder with the following format

```angular2html
H1:x_coordinate,y_coordinate
...
H3:x_coordinate,y_coordinate

Start_Time H1 P1 P2 ... PN H2  
...
Start_Time H3 P1 P3 ... PN H2
```

### Explanation of Output Format

Your solution should generate a solution file in txt format.


You can refer to the `sample_result.txt` to get the idea. The sample result is NOT optimal by any means. `bad_result.txt` shows examples of lines with bad format. 


#### Hospital Placement
* The first n lines corresponds to the placement of hospitals on the grid in the format ```Hi:x_coordinate,y_coordinate```.

* This denotes that the hospital 1 will be placed at (x_coordinate, y_coordinate).


#### Actions
Each action will be denoted by a line 

``` Start_Time Ha P1 P2 ... PN Hb  ```

* ```Start_Time``` denotes the time that the action will occur
* ```Ha``` denotes the hospital you would like to dispatch an ambulance from, where a is the hospital number
* ```P1 P2 P3``` (up to 4 entries) denotes the list of people the dispatched ambulance will pick up, in order of priority.
* ```Hb``` denotes the hospital you would like to drop the picked-up people at.
* Putting it together: a line of 

   ```16 H2 P31 P2 P42 H3``` 

   Denotes that at minute 16, we will 
   * dispatch an ambulance from Hospital 2, 
   * pick up Person 31, 
   * then pick up Person 2, 
   * then pick up Person 42, 
   * then drop them off at Hospital 3.

The validator will ignore invalid actions in cases such as when the input hospital has no ambulances at the time, or if the number of people exceeds 4.

Any people who expire before reaching the end hospital will not be counted towards your score.

### Validation
**Validation:** Once done with your code, you can then validate the result using the following:

###

`python3 validator.py` - this will use the input_data.txt and sample_result.txt as input and solution files. 

Or

`python3 validator.py <input_file> <solution_file>` - this will use the specified input file and validate your generated solution file.

#### Using Makefile
Check Submission section on what files to submit. After you create the makefile, you can also validate your code with:

`python3 driver.py`

This will test your solution (in the outputs folder) with against input_data.txt

## Submission

Create a makefile for running your code. Your code would be copied to `Solutions` folder. Make sure your makefile runs run the code and prints the result in the `Outputs` folder with your teamname in the format [team_name].txt

Submit your code file titled `[team_name]_algorithm.extension` and the makefile `[team_name].mk`.
