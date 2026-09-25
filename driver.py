import datetime
import os
import sys

def listdir_nohidden(path):
    for f in os.listdir(path):
        if not f.startswith('.'):
            yield f

files = listdir_nohidden("Makefiles")
for fname in files:
	team_name = fname.split(".")[0]
	fname = "Makefiles/" + team_name + ".mk"
	f = open(fname,"r")
	out_fname = "Outputs/" + team_name + ".txt"
	f.close()
	start = datetime.datetime.now()
	os.system('make -f' + fname)
	end = datetime.datetime.now()
	t = end - start
	if t.seconds >= 120:
		print("\n\tCode took too long to run. No points awarded.")
	os.system("python3 validator.py input_data.txt " + out_fname + " > Results/" + team_name + "_results.txt") 
	


