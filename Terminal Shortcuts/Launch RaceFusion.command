#!/bin/bash
cd "$HOME/Desktop/RaceFusion"
/usr/local/bin/streamlit run app.py || /opt/homebrew/bin/streamlit run app.py || python3 -m streamlit run app.py
