#!/bin/bash
cd ~/Desktop/RaceFusion
git add -A
echo "Enter a description of your changes:"
read msg
git commit -m "$msg"
git push origin main
echo ""
echo "Done! GitHub updated."
read -p "Press Enter to close..."
