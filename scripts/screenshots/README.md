# README screenshots

The images in `docs/images/` are taken from a local Home Assistant instance with the
demo integration. Two demo media players stand in for Kodi and a Music Assistant player;
the plugs are template switches. The cover art in `config/www/` is generated.

1. Link the integration into the config folder and start Home Assistant:

   ```bash
   ln -s "$PWD/../../custom_components" config/custom_components
   hass -c config
   ```

2. Onboard, create the area and set up the room through the real config flow, then save the dashboard:

   ```bash
   python setup_demo.py   # writes session.json (token, device id)
   python dashboard.py
   ```

3. Take the screenshots (Playwright with Chromium):

   ```bash
   node shoot.js ../../docs/images                       # all
   node shoot.js ../../docs/images dashboard,more-info   # some
   ```

   `dashboard-music.png` is the dashboard after selecting the music source with the display
   run-down set to 0.

`session.json` contains an access token for the throwaway instance; do not commit it.
