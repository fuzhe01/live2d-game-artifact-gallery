@echo off
cd /d D:\live2d-game-artifact-gallery
echo Gallery: http://127.0.0.1:8901/nikke/viewer/index.html
"C:\Python315\python.exe" -m http.server 8901 --bind 127.0.0.1
pause