#!/bin/sh
# keep the goflow2 output files small: truncate them once an hour (Fluent Bit has already shipped the lines; its tail DB
# follows the truncation). Hourly since evpn-clab's throughput tests write ~16k samples/s while they run.
while true; do sleep 3600; : > /flows/flows.json; : > /flows/evpn-clab.json; done
