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
3. *Einstellungen → Geräte & Dienste → Integration hinzufügen → Better Media Player*,
   einmal **pro Raum**.

## Einrichtung

| Schritt | Inhalt |
|---|---|
| Neuer Raum | Name und Bereich. Mit Bereich werden Kodi- und Music-Assistant-Player des Bereichs vorausgewählt. |
| Media Player | Video-Player (Kodi) und/oder Musik-Player (der Player des Raums, **keine** Gruppe). |
| Stromgeräte | Bild (TV), Lautsprecher (auch mehrere, z. B. Boxen + AVR), Subwoofer. |
| Player: Sonderfälle | Nur nötig, wenn ein Player sich nicht selbst schalten lässt (Ein-/Aus-Aktion) oder seine Hardware an einer Steckdose hängt. |
| Zeiten | Nachlauf, Leerlauf-Timeout, Start-Timeout, Wartezeiten nach dem Einschalten. |
| Standby abwarten | Optional, nur mit Leistungssensoren. |

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
Lautsprecher ein. In v0.1 gilt das für jede Gruppe. Die ersten 1–2 Sekunden können fehlen,
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

## Noch nicht in v0.1

- Gruppen-Richtlinien (z. B. „All Players (follow)" folgt der Anwesenheit), Anwesenheitssensoren,
  zentraler Eintrag. Das kommt mit v0.2.
- Prüfung per Leistungsmessung, ob ein Gerät wirklich läuft.
