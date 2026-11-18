# Paine: pressure gauges and water meters

This Home Assistant app reads mechanical instruments from local Tapo camera
images and publishes sensors via MQTT discovery. It supports Home Assistant
Green and x86-64. Both readers are calibrated for a specific installation:

- **Pressure (bar):** Reads a dark needle on a light, clockwise 0–4 bar dial.
  In **Configuration**, enter the pressure camera account and the angles of the
  2- and 3-bar marks. The tested gauge is readable in daylight and infrared.
- **Water (m³):** Locates and reads a mechanical wheel register, with optional mirroring. Set
  **Water meter — address** and its camera account for **Water meter total**
  and **Daily water consumption**. Use steady visible light: this meter’s red
  digits are practically unreadable under infrared night vision.

Enter the shared MQTT broker settings, save and start the app. Compare the first
readings with the physical instruments. See the **Documentation** tab for setup,
calibration and diagnostics.
