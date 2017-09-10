# TODO: Add optparse/argparse to control:
#   - clustering threshold
#   - capture duration per channel
#   - specifying SSIDs rather than sniffing

import re
import random
import json
import numpy as np
from math import sqrt
from os import system, popen
from time import sleep

# Returns
def sniff():
    oldnetwork_m = re.match("^Current Wi-Fi Network: (.*)$", popen("networksetup -getairportnetwork en0").read())
    if oldnetwork_m is not None:
        print "Disconnecting from network {}...".format(oldnetwork_m.group(1))
        system("sudo airport -z")
    else:
        print "No network connected."
        print "This script requires a network connection to connect to WiGLE."

    # TODO: Listen for all devices (not just those who send probe requests), also
    # display those who made no probe requests with an empty list of probed
    # networks. Then, listen again on whichever channel that device was busiest on.

    lines = []
    for ch in [1, 6, 11]:
        print "Changing channel to {}...".format(ch)
        system("sudo airport -c{}".format(ch))
        lines += popen("tshark -I -a duration:30 -f 'subtype probe-req' -Y 'wlan.fcs_good && wlan_mgt.ssid != \"\"'").readlines()

    p = re.compile(r"^ +\d+ +\d+\.\d+ (.*) -> Broadcast +802.11 \d+ Probe Request, SN=\d+, FN=0, Flags=\.{8}C, SSID=(.*)$", re.M)
    reqs = {}
    for line in lines:
        m = p.match(line)
        if not m: 
            # print "malformed line: {}".format(line)
            continue # watch out for "None" matches.
        mac_addr, req_ssid = m.group(1), m.group(2)
        if not mac_addr in reqs.keys(): reqs[mac_addr] = set([])
        reqs[mac_addr].add(req_ssid)

    devices = []
    print "Which device would you like to map?"
    for i, k in enumerate(reqs.keys()):
        print "({}) {}: {}".format(i, k, [n for n in reqs[k]])
        devices.append(k)
    while True:
        n = raw_input()
        try: 
            n = int(n)
            if n >= 0 and n < len(devices): break
            else: print "index out of range."
        except:
            continue

    return reqs[devices[n]]

def get_input(*args):
    while True:
        answer = raw_input()
        if answer in args:
            return answer

ssids = sniff()

print "Reconnecting WiFi adapter..."
system("networksetup -setairportpower en0 off")
sleep(0.2)
system("networksetup -setairportpower en0 on")
sleep(2.5)

newnetwork_m = re.match("^Current Wi-Fi Network: (.*)$", popen("networksetup -getairportnetwork en0").read())
if newnetwork_m is None:
    print "Could not connect to a network in order to query WiGLE."
    print "Would you like to try manually connecting and continue? (y/n)"
    answer = get_input('y', 'n')
    if answer == 'n': 
        print "SSIDs: "
        print ssids
        exit()
    elif answer == 'y':
        print "Please connect to a network now, then press enter."
        raw_input()

# Returns a string identical to s, except every space has been replaced by %20.
def convert_spaces(s):
    news = ""
    for i in xrange(len(s)):
        if s[i] == ' ':
            news = news + '%20'
        else:
            news = news + s[i]
    return news

# Queries WiGLE with the given parameters. The parameters should be in the form
# of a dictionary.  The "ssid" parameter is required.  Documentation on the
# WiGLE API can be found here:
# https://api.wigle.net/swagger#!/Network_search_and_information_tools/search
# Returns a dictionary containing the JSON reply from WiGLE, already parsed
# using json.loads(). Returns None if there is a problem querying WiGLE.
# TODO: Check the response more thoroughly, return None if it is empty.
def query_wigle(params):
    url = 'https://api.wigle.net/api/v2/network/search?onlymine=false&freenet=false&paynet=false'
    for k in params.keys():
        url += '&' + k + '=' + params[k]
    try:
        f = popen("curl -H 'Accept:application/json' -u YOUR:API_KEY '{}'".format(url))
        resp = json.loads(f.read())
        if not resp["success"]:
            print "ERROR from WiGLE: {}".format(resp["error"])
            return None
        print resp # DEBUG
        return resp
    except Exception as e:
        print "ERROR: {}".format(e)
        return None

# K-means clustering. Returns K or fewer centroids for the data.
# 
# Params:
#   K a maximum number of clusters to find.
#   X a numpy array of all the points.
#
# This function uses Lloyd's Algorithm, with code from
# https://datasciencelab.wordpress.com/2013/12/12/clustering-with-k-means-in-python/.
# 
# This function will return up to K tuples, one for each cluster.
def kmeans(K, X):
    # Some internal functions for Lloyd's Algorithm...
    def cluster_points(X, mu):
        clusters = {}
        for x in X:
            bestmukey = min([(i[0], np.linalg.norm(x-mu[i[0]])) for i in enumerate(mu)], key=lambda t:t[1])[0]
            try:
                clusters[bestmukey].append(x)
            except KeyError:
                clusters[bestmukey] = [x]
        return clusters

    def reevaluate_centers(mu, clusters):
        newmu = []
        keys = sorted(clusters.keys())
        for k in keys:
            newmu.append(np.mean(clusters[k], axis = 0))

        # If two mu are within 10 deg of each other, merge them.
        newnewmu = []
        merged = [False]*len(newmu)
        for i in xrange(len(newmu)):
            if merged[i]: continue
            merged[i] = True
            nearby = [False] * len(newmu)
            for j in xrange(i+1, len(newmu)):
                if np.linalg.norm(newmu[i]-newmu[j]) < 10.0:
                    nearby[i] = True
                    nearby[j] = True
            if nearby[i]:
                merge_us = [newmu[i]]
                for j in xrange(i+1, len(newmu)):
                    if nearby[j]:
                        merge_us.append(newmu[j])
                        merged[j] = True
                newnewmu.append(np.mean(merge_us, axis=0))
            else:
                newnewmu.append(newmu[i])
                    
        return newnewmu

    def has_converged(mu, oldmu):
        return (set([tuple(a) for a in mu]) == set([tuple(a) for a in oldmu]))

    # Initialize to K random centers
    # A few "seed" locations for clusters: US West, US East, Germany, Syria, S. Korea, Australia.
    # seed_mu = np.array([(47.5, -122.), (40.5, -74.), (50., 8.5), (34., 35.5), (35., 126.), (-34., 150.)])
    # Seed values, plus more random samples from the data for the remaining K.
    # print "K: {}, |seed_mu|: {}".format(K, len(seed_mu))
    # if (K - len(seed_mu)) > 0:
    #     mu = np.concatenate( (seed_mu, random.sample(X, (K-len(seed_mu)))) )
    # else:
    #     mu = seed_mu[:min(len(seed_mu), K)]
    # print mu
    mu = random.sample(X, K)

    while True:
        oldmu = mu
        # Assign all points in X to clusters
        clusters = cluster_points(X, mu)
        # Reevaluate centers
        mu = reevaluate_centers(oldmu, clusters)
        if has_converged(mu, oldmu): break
    return(mu, clusters)

# Returns the average distance between points and their centroids.
# A lower score indicates a better fit.
def cluster_fit(mu, clusters):
    acc = 0.0
    cnt = 0
    for k in clusters.keys():
        for p in clusters[k]:
            acc += np.linalg.norm(p-mu[k])
            cnt += 1
    return acc/cnt

# TODO: Allow the user to confine their search to just one continent.

colours = ["red", "blue", "green", "yellow", "orange", "purple"]
mapurl = "http://maps.google.com/maps/api/staticmap?size=640x640&maptype=roadmap"
markers = {}
for i, ssid in enumerate(ssids):
    print "Querying WiGLE for SSID \"{}\"...".format(ssid)
    print ""
    map_json = query_wigle({ 'ssid': convert_spaces(ssid), 'lastupdt': '20170101' })
    print ""
    if map_json is None:
        print "Problem communicating with WiGLE."
        exit()
    
    print "{} results found for \"{}\".".format(map_json["totalResults"], ssid)
    X = np.array([(r["trilat"], r["trilong"]) for r in map_json["results"] if\
        r["trilat"] != 0.0 or r["trilong"] != 0.0])

    if map_json["totalResults"] != map_json["resultCount"]:
        print "Only received the first {} results.".format(map_json["resultCount"])
    
    if len(X) > 10:
        print "Many results returned for SSID \"{}\".".format(ssid)
        print "Please choose what to do:"
        print "(1) Do clustering analysis and choose which clusters to keep"
        print "(2) Keep all results"
        print "(3) Skip this SSID (keep none)"
        choice = get_input('1', '2', '3')
        if choice == '1':
            # Run k-means 5 times with differen K values.
            mu, clusters = [None]*5, [None]*5
            print "Please choose a clustering."
            for j in xrange(5):
                K = int(round(sqrt(len(X))/(j+1)))
                if K == 0: continue
                mu[j], clusters[j] = kmeans(K, X)
                print "{}: {} clusters, fit score {}".format(j, len(mu[j]), cluster_fit(mu[j], clusters[j]))
            c = int(get_input('0', '1', '2', '3', '4'))
            print "Displaying clustering {}.".format(c)
            print ""
            for k in clusters[c].keys():
                print "Cluster {}:".format(k)
                print "---------------------"
                url_coords = ["&markers=color:{}|{},{}".format(colours[i], x[0], x[1]) for x in clusters[c][k]]
                print mapurl + ''.join(url_coords)
                print ""
            print "Which clusters would you like to keep? (format: '0, 1, 3')"
            cl_s = ""
            while True:
                cl_s = raw_input()
                if re.match(r"\d+([, ]*\d+)*", cl_s): break
            cl_a = map(int, cl_s.split(', '))
            markers[ssid] = np.concatenate( tuple([clusters[c][cl] for cl in cl_a]) )
            print markers[ssid]
        elif choice == '2':
            markers[ssid] = X
        else: # choice == '3'
            pass
    else:
        markers[ssid] = X

print ""
print "Map of all networks:"
markers_args = ""
for i, k in enumerate(markers.keys()):
    markers_args += ''.join(["&markers=color:{}|{},{}".format(colours[i], x[0], x[1]) for x in markers[k]])
print mapurl + markers_args
