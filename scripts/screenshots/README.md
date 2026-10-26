# Screenshots

The images in `docs/images/` (shown in the README and the pages in `docs/`) are taken from a local
Home Assistant instance (2026.10.3, English, light theme) with the demo integration:

- **Family Room:** Kodi, the demo's music player *Family Room Player*, TV, speakers and subwoofer on plugs
  (template switches) with power sensors.
- **Bedroom** and **Guest Room:** a Kodi each (Bedroom with a TV plug), so the Family Room can hand over.
- The Kodi players come from a stand-in for Better Kodi (`stand_ins/better_kodi`: players set up in YAML,
  with handover; not the real integration). The cover art in `config/www/` is generated.

1. Link the integration and the stand-in into the config folder and start Home Assistant:

   ```bash
   mkdir -p config/custom_components
   ln -s "$PWD/../../custom_components/better_media_player" config/custom_components/
   ln -s "$PWD/stand_ins/better_kodi" config/custom_components/
   hass -c config
   ```

2. Onboard and set up the rooms through the real config flow, then save the dashboard *Rooms*
   (since Home Assistant 2026.10, `/lovelace` shows the new overview):

   ```bash
   python setup_demo.py   # writes session.json (token, device id, entry id)
   python dashboard.py
   ```

   The rooms are set up without an area, which is assigned afterwards: since Home Assistant 2026.6 a new
   entity id starts with the area's name (`media_player.family_room_family_room` for a room named like its
   area), and the docs show the room's entities as `<domain>.<room>_...`.

3. Take the screenshots (Playwright with Chromium):

   ```bash
   python scene.py movie                                 # Kodi plays
   node shoot.js ../../docs/images                       # all but dashboard-music
   node shoot.js ../../docs/images dashboard,more-info   # some
   python scene.py music                                 # music plays, Kodi and the TV off
   node shoot.js ../../docs/images dashboard-music
   ```

   `device-settings.png` is the *Configuration* card of the room's device page (taken in a narrow window,
   so the names are not cut). `announce.png` is the action in the developer tools (filled in YAML mode,
   shown in UI mode).

4. Update the image versions in the README (its image URLs carry a hash of the file, so a changed image is not
   shown from a cache; a test fails while they are out of date):

   ```bash
   python ../readme_images.py
   ```

`session.json` contains an access token for the throwaway instance; do not commit it.
