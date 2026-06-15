import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "./styles/app.css";

const API = "http://127.0.0.1:19110";

function App() {
  const [profiles, setProfiles] = useState([]);
  const [selected, setSelected] = useState(null);
  const [messages, setMessages] = useState([]);
  const [command, setCommand] = useState("");
  const [status, setStatus] = useState("starting");
  const [hfTokenStatus, setHfTokenStatus] = useState({ configured: false, redacted: "unset" });
  const [hfTokenDialogOpen, setHfTokenDialogOpen] = useState(false);
  const [hfTokenInput, setHfTokenInput] = useState("");
  const [hfTokenRemember, setHfTokenRemember] = useState(false);
  const [hfTokenMessage, setHfTokenMessage] = useState("");

  async function refresh() {
    try {
      const [profilesRes, hfTokenRes] = await Promise.all([
        fetch(`${API}/profiles`),
        fetch(`${API}/secrets/hf-token`),
      ]);
      const rows = await profilesRes.json();
      const tokenStatus = await hfTokenRes.json();
      setProfiles(rows);
      setHfTokenStatus(tokenStatus);
      setSelected((current) => current || rows[0]?.id || null);
      setStatus("backend online");
    } catch (error) {
      setStatus(String(error));
    }
  }

  async function inspect(id) {
    setSelected(id);
    const [validation, rendered] = await Promise.all([
      fetch(`${API}/profiles/${id}/validate`, { method: "POST" }).then((r) => r.json()),
      fetch(`${API}/profiles/${id}/render`, { method: "POST" }).then((r) => r.json()),
    ]);
    setMessages(validation);
    setCommand(rendered.shell_command || "");
  }

  async function launch(id) {
    const res = await fetch(`${API}/profiles/${id}/launch`, { method: "POST" });
    alert(JSON.stringify(await res.json(), null, 2));
  }

  async function refreshHfTokenStatus() {
    const res = await fetch(`${API}/secrets/hf-token`);
    const tokenStatus = await res.json();
    setHfTokenStatus(tokenStatus);
  }

  async function setHfToken(event) {
    event.preventDefault();
    setHfTokenMessage("");
    const rememberRequested = hfTokenRemember;
    const res = await fetch(`${API}/secrets/hf-token`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ token: hfTokenInput, remember: rememberRequested }),
    });
    const tokenStatus = await res.json();
    if (!res.ok) {
      setHfTokenMessage(tokenStatus.detail || "Could not set HF_TOKEN.");
      return;
    }
    setHfTokenInput("");
    setHfTokenRemember(false);
    setHfTokenStatus(tokenStatus);
    setHfTokenMessage(
      rememberRequested
        ? "HF_TOKEN set and remembered on this machine."
        : tokenStatus.persistent_configured
        ? "HF_TOKEN set for this backend session. Existing remembered token is unchanged."
        : "HF_TOKEN set for this backend session."
    );
  }

  async function clearHfToken() {
    setHfTokenMessage("");
    const res = await fetch(`${API}/secrets/hf-token`, { method: "DELETE" });
    const tokenStatus = await res.json();
    setHfTokenInput("");
    setHfTokenRemember(false);
    setHfTokenStatus(tokenStatus);
    setHfTokenMessage("HF_TOKEN cleared from this backend process and local remembered storage.");
  }

  function closeHfTokenDialog() {
    setHfTokenInput("");
    setHfTokenRemember(false);
    setHfTokenMessage("");
    setHfTokenDialogOpen(false);
  }

  useEffect(() => {
    refresh();
  }, []);

  useEffect(() => {
    if (selected) inspect(selected);
  }, [selected]);

  return (
    <main>
      <header>
        <div>
          <p>Local Inference Dockyard</p>
          <h1>Model Control Plane</h1>
        </div>
        <div className="header-actions">
          <button
            onClick={() => {
              refreshHfTokenStatus();
              setHfTokenDialogOpen(true);
            }}
          >
            HF Token: {hfTokenStatus.configured ? "set" : "not set"}
          </button>
          <button onClick={refresh}>Refresh</button>
        </div>
      </header>
      <section className="status">Backend: {status}</section>
      {hfTokenDialogOpen && (
        <div className="modal-backdrop" role="presentation">
          <form className="modal" onSubmit={setHfToken}>
            <div className="modal-title">
              <div>
                <p>Process environment</p>
                <h2>HF_TOKEN</h2>
              </div>
              <button type="button" onClick={closeHfTokenDialog}>Close</button>
            </div>
            <div className="secret-status">
              Status: <strong>{hfTokenStatus.configured ? "set" : "not set"}</strong>
              <span>Process: {hfTokenStatus.process_configured ? "set" : "not set"}</span>
              <span>Remembered: {hfTokenStatus.persistent_configured ? "set" : "not set"}</span>
              <span>Path source: {hfTokenStatus.token_path_source || "dockyard_state"}</span>
            </div>
            <label className="field">
              <span>HF_TOKEN</span>
              <input
                type="password"
                value={hfTokenInput}
                onChange={(event) => setHfTokenInput(event.target.value)}
                autoComplete="off"
                spellCheck="false"
              />
            </label>
            <label className="check-field">
              <input
                type="checkbox"
                checked={hfTokenRemember}
                onChange={(event) => setHfTokenRemember(event.target.checked)}
              />
              <span>Remember on this machine</span>
            </label>
            <div className="secret-note">
              {hfTokenStatus.restart_notice || "Session-only unless remembered on this machine."}
              {" "}
              {hfTokenStatus.inheritance_notice || "Set before model pulls or launches."}
            </div>
            {hfTokenMessage && <div className="secret-message">{hfTokenMessage}</div>}
            <div className="actions">
              <button type="submit">Set</button>
              <button type="button" onClick={clearHfToken}>Clear</button>
            </div>
          </form>
        </div>
      )}
      <div className="grid">
        <section className="panel">
          <h2>Saved Profiles</h2>
          {profiles.map((profile) => (
            <button
              className={profile.id === selected ? "row active" : "row"}
              key={profile.id}
              onClick={() => inspect(profile.id)}
            >
              <strong>{profile.name}</strong>
              <span>{profile.backend} · {profile.host_port}</span>
              <small>{profile.warnings} warnings · {profile.errors} errors</small>
            </button>
          ))}
        </section>
        <section className="panel detail">
          <h2>Rendered Command</h2>
          <pre>{command || "Select a profile."}</pre>
          <div className="actions">
            <button onClick={() => selected && inspect(selected)}>Validate / Render</button>
            <button onClick={() => selected && launch(selected)}>Launch</button>
          </div>
          <h2>Validation</h2>
          {messages.map((message) => (
            <div className={`message ${message.level}`} key={message.code + message.message}>
              <strong>{message.level}</strong>
              <span>{message.message}</span>
            </div>
          ))}
        </section>
      </div>
    </main>
  );
}

createRoot(document.getElementById("root")).render(<App />);
