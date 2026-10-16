"use strict";

const status = document.getElementById("status");
const form = document.getElementById("editor");
let settings;
let revision;
let dirty = false;
let busy = false;

// [key, label, type, required, min, max, default]
const defaults = [
  ["mqtt_host", "MQTT broker", "text", true, null, null, "core-mosquitto"],
  ["mqtt_port", "MQTT port", "number", true, 1, 65535, 1883],
  ["mqtt_user", "MQTT username", "text", false, null, null, ""],
  ["mqtt_password", "MQTT password", "password", false, null, null, ""],
  ["mqtt_tls", "MQTT TLS", "boolean", false, null, null, false],
  ["capture_interval", "Reading interval (seconds)", "number", true, 15, 86400, 60],
  ["capture_timeout", "Network timeout (seconds)", "number", true, 1, 300, 10],
  ["shot_count", "Shots per reading (median)", "number", true, 1, 9, 3],
];
const cameraFields = [
  ["sensor_id", "Sensor ID (unique)", "text", true],
  ["sensor_name", "Sensor name", "text", false],
  ["camera_host", "Camera address (host or host:port)", "text", true],
  ["camera_user", "Camera username", "text", true],
  ["camera_password", "Camera password", "password", true],
  ["two_bar_angle", "2-bar tick angle (degrees)", "number", true, 0, 359.99],
  ["three_bar_angle", "3-bar tick angle (degrees)", "number", true, 0, 359.99],
];
const identityFields = [
  ["device_id", "Device ID (unique)", "text", false],
  ["device_name", "Device name", "text", false],
  ["state_topic", "MQTT state topic (unique on broker)", "text", false],
];

function message(text, error = false) {
  status.textContent = text;
  status.classList.toggle("error", error);
}

async function request(path, body) {
  const response = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    cache: "no-store",
    headers: {"Content-Type": "application/json", "X-Paine-Editor": "1"},
    ...(body === undefined ? {} : {body: JSON.stringify(body)}),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "Configuration request failed");
  return result;
}

function addFields(container, target, fields, inherited = false) {
  for (const [key, title, type, required, min, max, fallback] of fields) {
    const label = document.createElement("label");
    label.append(title + (required && !inherited ? " *" : ""));
    const secretRef = typeof target[key] === "string" && target[key].startsWith("!secret ");
    const input = document.createElement(type === "boolean" ? "select" : "input");
    input.name = key;
    if (type === "boolean") {
      for (const [value, text] of (inherited ? [["", "Inherit shared setting"], ["false", "Off"], ["true", "On"]] : [["false", "Off"], ["true", "On"]])) {
        const option = document.createElement("option");
        option.value = value;
        option.textContent = text;
        input.append(option);
      }
      // Preserve a secret reference, even for a boolean, until explicitly changed.
      if (secretRef) {
        const option = document.createElement("option");
        option.value = target[key];
        option.textContent = "Existing HA secret reference";
        input.append(option);
      }
      input.value = target[key] === undefined ? (inherited ? "" : "false") : String(target[key]);
    } else {
      input.type = secretRef && type === "number" ? "text" : type;
      input.value = target[key] ?? (inherited ? "" : fallback ?? "");
      input.required = required && !inherited;
      input.autocomplete = type === "password" ? "new-password" : "off";
      if (type === "number" && !secretRef) {
        if (min != null) input.min = min;
        if (max != null) input.max = max;
        input.step = key.endsWith("_angle") ? "any" : "1";
      }
      if (inherited) input.placeholder = String(settings[key] ?? fallback ?? "Inherit shared setting");
      // Never use a password (or secret reference) as a plaintext placeholder.
      if (inherited && type === "password") input.placeholder = "Inherit shared password";
    }
    input.addEventListener("input", () => {
      dirty = true;
      if (input.value === "" && !input.required) {
        if (!inherited && (key === "mqtt_user" || key === "mqtt_password")) target[key] = "";
        else delete target[key];
      } else if (input.value.startsWith("!secret ")) target[key] = input.value;
      else if (type === "boolean") target[key] = input.value === "true";
      else if (type === "number") target[key] = input.value === "" ? "" : Number(input.value);
      else target[key] = input.value;
    });
    label.append(input);
    container.append(label);
  }
}

function render() {
  const shared = document.getElementById("shared");
  shared.replaceChildren();
  addFields(shared, settings, defaults);
  const cameras = document.getElementById("cameras");
  cameras.replaceChildren();
  settings.gauges.forEach((camera, index) => {
    const card = document.createElement("section");
    card.className = "card";
    const heading = document.createElement("h2");
    heading.textContent = `Camera ${index + 1}: ${camera.sensor_name || camera.sensor_id || "New gauge"}`;
    const help = document.createElement("p");
    help.textContent = "Calibrate the 2-bar and 3-bar tick angles separately for this view. Angles increase clockwise from image-right; 3-bar must be less than 90° clockwise from 2-bar.";
    const fields = document.createElement("div");
    fields.className = "fields";
    addFields(fields, camera, cameraFields);
    const advanced = document.createElement("details");
    const summary = document.createElement("summary");
    summary.textContent = "Advanced: identity, topic, timing and broker overrides";
    const overrides = document.createElement("div");
    overrides.className = "fields";
    addFields(overrides, camera, identityFields);
    addFields(overrides, camera, defaults, true);
    advanced.append(summary, overrides);
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "remove";
    remove.textContent = "Remove camera";
    remove.addEventListener("click", () => {
      if (!confirm("Remove this camera from Paine? Its retained MQTT entity is not deleted automatically.")) return;
      settings.gauges.splice(index, 1);
      dirty = true;
      render();
    });
    card.append(heading, help, fields, advanced, remove);
    cameras.append(card);
  });
  form.hidden = false;
}

async function load() {
  const result = await request("api/config");
  settings = result.options;
  revision = result.revision;
  dirty = false;
  render();
}

document.getElementById("add").addEventListener("click", () => {
  let number = settings.gauges.length + 1;
  while (settings.gauges.some(camera => camera.sensor_id === `pressure_${number}`)) number++;
  settings.gauges.push({sensor_id: `pressure_${number}`, camera_host: "", camera_user: "", camera_password: ""});
  dirty = true;
  render();
  document.getElementById("cameras").lastElementChild.scrollIntoView({behavior: "smooth"});
});
document.getElementById("reload").addEventListener("click", async () => {
  if (dirty && !confirm("Discard unsaved changes and reload?")) return;
  try { await load(); message("Loaded saved settings."); }
  catch (error) { message(error.message, true); }
});
form.addEventListener("submit", async event => {
  event.preventDefault();
  if (busy) return;
  busy = true;
  // Disable the entire form while saving so edits cannot be silently overwritten.
  for (const control of form.querySelectorAll("input, select, button")) control.disabled = true;
  try {
    const result = await request("api/config", {options: settings, revision});
    dirty = false;
    await load();
    message(result.message);
  } catch (error) { message(error.message, true); }
  finally {
    busy = false;
    for (const control of form.querySelectorAll("input, select, button")) control.disabled = false;
  }
});
window.addEventListener("beforeunload", event => {
  if (dirty) { event.preventDefault(); event.returnValue = ""; }
});
load().then(() => message("Settings loaded. Add cameras below; no YAML editing needed."))
  .catch(error => message(error.message, true));
