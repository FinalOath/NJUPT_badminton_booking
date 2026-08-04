@echo off
netsh advfirewall firewall delete rule name="mitmweb" >nul 2>nul
netsh advfirewall firewall add rule name="mitmweb" dir=in action=allow protocol=TCP localport=8080
sc stop MpsSvc >nul 2>nul
echo Done. Now try Shadowrocket again.
pause
