#!/bin/sh
# keep the goflow2 output file small: truncate it once a day (Fluent Bit has already shipped the lines; its tail DB follows the truncation)
while true; do sleep 86400; : > /flows/flows.json; done
