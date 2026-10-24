#!/bin/bash
# One unedited take of the LEFT monitor of display :1: real Isaac Sim GUI + live ops panel, plus real Gmail (Hyperion search only).
export DISPLAY=:1; cd "$(dirname "$0")"; TAG=${TAG:-gui1}; LOG=runs/ops_$TAG.log; OUT=takes/${TAG}_take.mp4
EDGE=$(xdotool search --name "Microsoft" | tail -1)
gmail_show() {   # bring Edge (Gmail search "Hyperion") onto the recorded monitor for $1 seconds, then put it back
  wmctrl -i -r $EDGE -b remove,maximized_vert,maximized_horz; wmctrl -i -r $EDGE -e 0,0,0,1920,1080; xdotool windowactivate --sync $EDGE
  xdotool key ctrl+l; xdotool type --delay 10 "https://mail.google.com/mail/u/0/#search/Hyperion"; xdotool key Return; sleep 3; xdotool key F5; sleep "$1"
  wmctrl -i -r $EDGE -e 0,1920,0,1920,1080; wmctrl -i -r $EDGE -b add,maximized_vert,maximized_horz
  xdotool windowactivate $(xdotool search --name "Isaac Sim Python" | tail -1); xdotool windowactivate $(xdotool search --name "Hyperion Ops" | tail -1)
}
ffmpeg -loglevel error -y -f x11grab -draw_mouse 1 -framerate 20 -video_size 1920x1080 -i :1+0,0 -c:v libx264 -preset veryfast -crf 20 -pix_fmt yuv420p $OUT &
REC=$!; date +%s.%N > runs/${TAG}_take_start.txt; sleep 8
xdotool mousemove 1852 72 click 1; echo "inject clicked $(date +%T)"            # the panel's "Inject fault · robot 7" button
until grep -q "technician_en_route\|needs_human" $LOG; do sleep 2; done
if grep -q needs_human $LOG; then sleep 8; curl -s -X POST localhost:8300/approve -d '{"incident":"INC-1"}'; echo " approved $(date +%T)"; fi
sleep 6; gmail_show 10; echo "gmail 1 $(date +%T)"
until grep -q "incident_closed" $LOG; do sleep 2; done
sleep 8; gmail_show 9; echo "gmail 2 $(date +%T)"; sleep 4
kill -INT $REC; wait $REC; echo "take done $(date +%T)"
