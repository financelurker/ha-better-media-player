# Better Media Player – Anleitung

Ein Raum-Media-Player für Home Assistant. Er fasst die Media Player eines Raums (z. B. Kodi
und einen Music-Assistant-/sendspin-Player) mit den Steckdosen für TV, Lautsprecher und
Subwoofer zu **einer** Entität zusammen.

## Voraussetzungen

- **Jede Steckdose gehört genau einem Regler.** Bestehende Automationen, Skripte oder Helfer,
  die dieselben Steckdosen schalten, vor der Einrichtung deaktivieren. Am Steckdosen-Switch
  unter *Einstellungen → Entität → Zugehörig* sieht man, wer sie sonst noch benutzt.
- **Geräte starten bei Stromzufuhr selbst** (TV, Aktivboxen, AVR), oder sie lassen sich anders
  einschalten.
- **Kodi:** `turn_on`/`turn_off` lösen bei Kodi nur die Device-Trigger aus. Deine
  Wake-on-LAN- und Suspend-Automationen dafür **bleiben aktiv**, sie sind die Ausführung.

## Installation

1. Ordner `custom_components/better_media_player` nach `/config/custom_components/` kopieren
   (oder über HACS als benutzerdefiniertes Repository hinzufügen).
2. Home Assistant neu starten.
3. *Einstellungen → Geräte & Dienste → Integration hinzufügen → Better Media Player →
   Einen Raum*, einmal **pro Raum**.
4. Für Follow-Me zusätzlich einmal *Zentrale Einstellungen* hinzufügen.

## Einrichtung

| Schritt | Inhalt |
|---|---|
| Neuer Raum | Name und Bereich. Mit Bereich werden Kodi- und Music-Assistant-Player des Bereichs vorausgewählt. |
| Media Player | Video-Player (Kodi) und/oder Musik-Player (der Player des Raums, **keine** Gruppe). |
| Stromgeräte | Bild (TV), Lautsprecher (auch mehrere, z. B. Boxen + AVR), Subwoofer. |
| Player: Sonderfälle | Nur nötig, wenn ein Player sich nicht selbst schalten lässt (Ein-/Aus-Aktion) oder seine Hardware an einer Steckdose hängt. |
| Zeiten | Nachlauf, Leerlauf-Timeout, Start-Timeout, Wartezeiten nach dem Einschalten. |
| Standby abwarten | Optional, nur mit Leistungssensoren. |
| Follow-Me | Anwesenheitssensoren, Nachlauf der Anwesenheit, Lautstärke-Offset, Vorwärmen. Ohne Anwesenheitssensoren nimmt der Raum nicht teil. |

Alles lässt sich später unter *Konfigurieren* ändern.

## Verhalten

**Einschalten** (Power-Button der Raum-Entität): Es startet die zuletzt genutzte Quelle,
beim ersten Mal Kodi. Die zuletzt genutzte Quelle wird gespeichert und übersteht Neustarts.
Über die Quellen-Auswahl wählt man explizit; die andere Quelle wird dabei beendet
(Kodi aus, Musik verlässt die Gruppe bzw. wird pausiert).

**Reihenfolge**

- Ein: Steckdose der Player-Hardware → Bild → Player wecken und warten, bis er bereit ist →
  Lautsprecher → Subwoofer.
- Aus: Subwoofer → Lautsprecher → Bild → Player-Hardware.

Ist der Player nach dem Start-Timeout (90 s) nicht bereit, wird alles Eingeschaltete wieder
abgeschaltet und der Status steht auf *Fehler*.

**Bedarf und Nachlauf**

| Teil | Wird gebraucht, solange … | Nachlauf |
|---|---|---|
| Bild | der Video-Player an ist (auch im Menü oder pausiert) | 10 min |
| Lautsprecher, Subwoofer | der Video-Player an ist **oder** Musik spielt | 2 min |
| Player-Hardware | der zugehörige Player aktiv ist | 2 min |

Pausierte Musik zählt nicht als Bedarf: nach 2 min gehen die Lautsprecher aus, beim
Weiterspielen wieder an. Die Nachlaufzeiten sind auch als Zahl-Entitäten einstellbar.

**Musik startet von selbst** (z. B. über eine Music-Assistant-Gruppe): Der Raum schaltet seine
Lautsprecher ein. Ohne zentrale Einstellungen gilt das für jede Gruppe, mit ihnen nur für
Gruppen mit Richtlinie *immer* oder *folgt Anwesenheit*. Die ersten 1–2 Sekunden können fehlen,
während der Verstärker hochfährt.

**Kodi im Menü**: Läuft Kodi 30 min ohne Wiedergabe, wird es ausgeschaltet.

**Raum aus** (Power-Button oder Button *Alles aus*):

1. Ist der Musik-Player in einer Gruppe, wird er per `media_player.unjoin` gelöst. War er das
   letzte Mitglied, pausiert Music Assistant die Gruppe.
2. Sonst wird direkte Wiedergabe gestoppt.
3. Kodi wird ausgeschaltet.
4. Alle Steckdosen gehen in der Aus-Reihenfolge sofort aus (Standby-Warten gilt weiter).

Braucht Kodi länger für den Suspend, schaltet der Raum deswegen nicht wieder ein. Erst eine
neue Wiedergabe gilt wieder als Wunsch.

**Manuell**: Schaltet jemand eine der Steckdosen von Hand, steht der Status auf *Manuell* und
der Raum schaltet nichts mehr automatisch. Das endet, sobald alle Steckdosen des Raums aus
sind, oder mit einem Befehl an die Raum-Entität. Was dann noch spielt, schaltet den Raum
nicht sofort wieder ein.

**Automatisch schalten** (Switch): Aus = keine automatische Schaltung mehr. Befehle an die
Raum-Entität funktionieren weiterhin.

**Standby abwarten** (optional): Mit Leistungssensor wird die Steckdose erst getrennt, wenn die
Leistung 30 s unter der Schwelle liegt, spätestens nach der Maximalzeit. Sinnvoll z. B. bei
OLED-TVs, die im Standby eine Panelpflege machen. Ohne CEC geht der TV nie selbst in den
Standby; dann bringt diese Option nichts und es gilt nur der Nachlauf.

## Follow-Me

Braucht den Eintrag **Zentrale Einstellungen**. Dort bekommt jeder Music-Assistant-Gruppen-Player
eine Richtlinie:

| Richtlinie | Verhalten |
|---|---|
| Folgt Anwesenheit | Die Musik folgt den Personen (siehe unten). |
| Immer | Wiedergabe in der Gruppe schaltet die Lautsprecher ihrer Mitglieder ein. |
| *(nicht aufgeführt)* | Ignoriert: Wiedergabe in der Gruppe schaltet nichts ein. |

Solange eine Follow-Gruppe spielt:

- **Vorwärmen:** Die Lautsprecher aller Follow-Räume gehen an, damit der Ton beim Betreten
  sofort da ist.
- **Beitritt:** Ein Raum mit Anwesenheit tritt der Gruppe bei. Vorher wird seine Lautstärke
  gesetzt: Gruppenlautstärke plus Offset des Raums (z. B. Schlafzimmer −10 Prozentpunkte).
  Hat die Gruppe gerade 0 %, gilt die zuletzt gemerkte Lautstärke, sonst der Standard (25 %).
- **Verlassen:** Ohne Anwesenheit verlässt der Raum die Gruppe nach seinem Nachlauf (2 min).
  Verlässt der letzte Raum die Gruppe, pausiert Music Assistant sie; die Lautsprecher gehen
  nach ihrem Nachlauf aus.
- **Kein Raub:** Spielt ein Raum etwas anderes (direkt oder in einer anderen Gruppe), bleibt er
  unberührt.
- **Ausgenommen:** Räume mit laufendem Video (Kodi; nach dem Ende wieder dabei; war es der
  letzte Raum, spielt die Gruppe dann weiter, innerhalb von 3 h), Räume im
  **Ruhe**-Modus (Switch pro Raum; verlässt sofort, Lautsprecher sofort aus) und Räume, die in
  dieser Sitzung ausgeschaltet wurden (bis die Sitzung endet).
- **Anwesenheit unbekannt** (alle Sensoren nicht verfügbar): keine Änderung. Die Musik bricht
  also nicht ab, nur weil die Sensor-Integration hängt.
- **Manuell oder per Ersatzraum gestartet:** Räume ohne Anwesenheit bleiben, solange in keinem
  Follow-Raum jemand erkannt wird. Danach gelten die normalen Regeln, inklusive Nachlauf.
- **Von dir pausiert:** Die Sitzung läuft weiter, die Räume bleiben, wie sie sind, die vorgewärmten
  Lautsprecher laufen nach. Beim Fortsetzen gelten wieder die Regeln.
- Es spielt immer nur eine Follow-Gruppe; startet eine andere, wird die ältere geleert (sie
  pausiert).

Eine leere Gruppe lässt sich nicht starten. Gestartet wird daher in Music Assistant mit
mindestens einem Raum, über den Button **Follow starten** oder per Skript/Automation:

```yaml
action: better_media_player.start_follow
data:
  group: media_player.all_players_follow   # optional, Standard: erste Follow-Gruppe
  media_content_id: "library://radio/42"   # optional, Standard: letzte Queue fortsetzen
  media_content_type: music                # optional
  volume: 0.15                             # optional, Gruppenlautstärke 0-1; Offsets gelten weiter
  fallback_rooms:                          # optional, falls niemand erkannt wird (Räume mit Anwesenheitssensoren)
    - media_player.schlafzimmer
```

> Gruppen-Player mit **statischen** Mitgliedern werden nicht unterstützt: Music Assistant
> stoppt die ganze Gruppe, wenn ein statisches Mitglied entfernt wird.

## Media Browser und Suche

Der Media Browser der Raum-Entität bündelt die Browser ihrer Player. Angeboten wird nur, was der
jeweilige Player kann.

| Eintrag | Inhalt |
|---|---|
| Warteschlange: \<Video-Player\> | die Warteschlange des Video-Players, falls er eine hat (z. B. Better Kodi) |
| Warteschlange: \<Musik-Player\> | Music Assistant: aktuelles und nächstes Element (mehr gibt HA nicht her) |
| \<Video-Player\> | sein Browser. Ist er aus: die HA-Medienquellen (z. B. Jellyfin) und ein Hinweis |
| \<Musik-Player\> | sein Browser (Künstler, Alben, Playlists, Radio …) |

- **Jetzt abspielen** aus dem Browser schaltet den Raum auf diesen Player um: Der andere Player wird
  beendet, die Einschaltsequenz läuft zuerst. Ein Film bei schlafendem Kodi schaltet also den TV ein,
  weckt Kodi und spielt dann ab. Ein Musik-Player in einer Gruppe verlässt sie vorher, damit sich nur
  dieser Raum ändert.
- **Musiktitel** öffnen einen kleinen Ordner mit *Jetzt abspielen*, *Als Nächstes abspielen*,
  *Zur Warteschlange hinzufügen* und *Warteschlange ersetzen* (wenn der Player das unterstützt).
  *Als Nächstes* und *Hinzufügen* ändern sonst nichts im Raum.
- **Suche** geht an die Player, die suchen können (Music Assistant). Sprachbefehle wie „spiele Kid Rock
  im Wohnzimmer" gehen an die Raum-Entität und schalten den Raum ein.
- Wählst du in einer Automation über die Raum-Entität ein Medium aus, wird eine ID gespeichert, die nur
  die Raum-Entität versteht.

## Status

| Status | Bedeutung |
|---|---|
| Aus | alles stromlos |
| Startet | Einschaltsequenz läuft |
| An | eine Quelle ist aktiv, spielt aber nicht (z. B. Kodi im Menü) |
| Spielt | eine Quelle spielt |
| Nachlauf | Steckdosen noch an, kein Bedarf mehr |
| Wartet auf Standby | Abschaltung wartet auf den Standby des Geräts |
| Manuell | von Hand geschaltet, Automatik pausiert |
| Fehler | Start fehlgeschlagen (Grund im Problem-Sensor) |
| Automatik aus | Switch *Automatisch schalten* ist aus |

| Follow-Me-Status (Sensor pro Raum) | Bedeutung |
|---|---|
| Inaktiv | keine Follow-Gruppe spielt |
| Folgt | Raum ist in der spielenden Follow-Gruppe |
| Wartet auf Anwesenheit | vorgewärmt, wartet auf jemanden |
| Bleibt (manueller Start) | ohne Anwesenheit behalten |
| Ausgenommen: Ruhe / Video / Raum aus | aus diesen Gründen nicht dabei |
| Ausgenommen: spielt anderswo | spielt direkt oder in einer anderen Gruppe |
| Anwesenheit unbekannt | Sensoren nicht verfügbar, keine Änderung |

## Noch nicht enthalten

- Prüfung per Leistungsmessung, ob ein Gerät wirklich läuft.
- Gruppen-Player mit statischen Mitgliedern.
