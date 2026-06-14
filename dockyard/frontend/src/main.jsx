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

  async function refresh() {
    try {
      const res = await fetch(`${API}/profiles`);
      const rows = await res.json();
      setProfiles(rows);
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
        <button onClick={refresh}>Refresh</button>
      </header>
      <section className="status">Backend: {status}</section>
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
