import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "./styles/app.css";

const API = import.meta.env.VITE_MODEL_PLANE_API || `${window.location.protocol}//${window.location.hostname}:19110`;

function App() {
  const [profiles, setProfiles] = useState([]);
  const [runs, setRuns] = useState([]);
  const [moeCards, setMoeCards] = useState([]);
  const [moeCardResults, setMoeCardResults] = useState({});
  const [selected, setSelected] = useState(null);
  const [selectedRun, setSelectedRun] = useState(null);
  const [messages, setMessages] = useState([]);
  const [command, setCommand] = useState("");
  const [integrationPreview, setIntegrationPreview] = useState(null);
  const [runBundle, setRunBundle] = useState(null);
  const [copyMessage, setCopyMessage] = useState("");
  const [status, setStatus] = useState("starting");
  const [hfTokenStatus, setHfTokenStatus] = useState({ configured: false, redacted: "unset" });
  const [hfTokenDialogOpen, setHfTokenDialogOpen] = useState(false);
  const [hfTokenInput, setHfTokenInput] = useState("");
  const [hfTokenRemember, setHfTokenRemember] = useState(false);
  const [hfTokenMessage, setHfTokenMessage] = useState("");
  const [manualEvidenceCard, setManualEvidenceCard] = useState(null);
  const [manualEvidenceInput, setManualEvidenceInput] = useState("");
  const [manualEvidenceNotes, setManualEvidenceNotes] = useState("");
  const [manualEvidenceMessage, setManualEvidenceMessage] = useState("");

  async function refresh() {
    try {
      const [profilesRes, hfTokenRes, runsRes, moeCardsRes] = await Promise.all([
        fetch(`${API}/profiles`),
        fetch(`${API}/secrets/hf-token`),
        fetch(`${API}/runs`),
        fetch(`${API}/moe-test-cards`),
      ]);
      const rows = await profilesRes.json();
      const tokenStatus = await hfTokenRes.json();
      const runRows = await runsRes.json();
      const cardRows = await moeCardsRes.json();
      setProfiles(rows);
      setRuns(runRows);
      setMoeCards(cardRows);
      setHfTokenStatus(tokenStatus);
      setSelected((current) => current || rows[0]?.id || null);
      setSelectedRun((current) =>
        runRows.some((run) => run.run_id === current) ? current : runRows[0]?.run_id || null
      );
      setStatus("backend online");
    } catch (error) {
      setStatus(String(error));
    }
  }

  async function inspect(id) {
    setSelected(id);
    const [validation, rendered, preview] = await Promise.all([
      fetch(`${API}/profiles/${id}/validate`, { method: "POST" }).then((r) => r.json()),
      fetch(`${API}/profiles/${id}/render`, { method: "POST" }).then((r) => r.json()),
      fetch(`${API}/profiles/${id}/integration-preview`).then((r) => r.json()),
    ]);
    setMessages(validation);
    setCommand(rendered.shell_command || "");
    setIntegrationPreview(preview);
  }

  async function launch(id) {
    const res = await fetch(`${API}/profiles/${id}/launch`, { method: "POST" });
    alert(JSON.stringify(await res.json(), null, 2));
    refresh();
  }

  async function runMoeCard(cardId, mode) {
    if (mode === "smoke") {
      const approved = window.confirm("Run one MoE smoke prompt through the selected local model?");
      if (!approved) return;
    }
    setMoeCardResults((current) => ({
      ...current,
      [cardId]: { ok: null, mode, status: "running" },
    }));
    const res = await fetch(`${API}/moe-test-cards/${cardId}/${mode}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: mode === "smoke" ? JSON.stringify({ approved_prompt_traffic: true }) : undefined,
    });
    const result = await res.json();
    setMoeCardResults((current) => ({
      ...current,
      [cardId]: { ...result, status: res.ok ? "complete" : "failed" },
    }));
  }

  function manualEvidenceTemplate(card) {
    const template = {};
    const fields = card.manual_evidence_schema?.fields || [];
    fields.forEach((field) => {
      template[field.name] = field.required ? "" : null;
    });
    template.app_runtime = card.runtime_stack || template.app_runtime;
    template.model_id = card.model && card.model !== "not-applicable" ? card.model : template.model_id;
    return JSON.stringify(template, null, 2);
  }

  function openManualEvidence(card) {
    setManualEvidenceCard(card);
    setManualEvidenceInput(manualEvidenceTemplate(card));
    setManualEvidenceNotes("");
    setManualEvidenceMessage("");
  }

  function closeManualEvidence() {
    setManualEvidenceCard(null);
    setManualEvidenceInput("");
    setManualEvidenceNotes("");
    setManualEvidenceMessage("");
  }

  async function recordManualEvidence(event) {
    event.preventDefault();
    if (!manualEvidenceCard) return;
    setManualEvidenceMessage("");
    let evidence;
    try {
      evidence = JSON.parse(manualEvidenceInput);
    } catch (error) {
      setManualEvidenceMessage(`Invalid JSON: ${error.message}`);
      return;
    }
    const res = await fetch(`${API}${manualEvidenceCard.manual_evidence_endpoint}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        approved_manual_evidence: true,
        evidence,
        notes: manualEvidenceNotes || null,
      }),
    });
    const result = await res.json();
    setMoeCardResults((current) => ({
      ...current,
      [manualEvidenceCard.card_id]: { ...result, status: res.ok ? "complete" : "failed" },
    }));
    if (!res.ok) {
      setManualEvidenceMessage(result.detail || "Could not record evidence.");
      return;
    }
    setManualEvidenceMessage(`Recorded ${result.run_id}.`);
    refresh();
  }

  async function inspectRun(id) {
    setSelectedRun(id);
    const bundle = await fetch(`${API}/runs/${id}/integration-bundle`).then((r) => r.json());
    setRunBundle(bundle);
  }

  async function checkRunBundle() {
    if (!selectedRun) return;
    const bundle = await fetch(`${API}/runs/${selectedRun}/integration-bundle/check`, { method: "POST" }).then((r) => r.json());
    setRunBundle(bundle);
  }

  async function copyText(label, text) {
    if (!text) return;
    await navigator.clipboard.writeText(text);
    setCopyMessage(`${label} copied.`);
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

  useEffect(() => {
    if (selectedRun) inspectRun(selectedRun);
  }, [selectedRun]);

  function HarnessBundle({ bundle, checkable }) {
    if (!bundle) {
      return <div className="muted">No integration bundle selected.</div>;
    }
    const hermesYaml = bundle.config_snippets?.hermes?.yaml || "";
    const openclawYaml = bundle.config_snippets?.openclaw?.yaml || "";
    const checks = bundle.connectivity_checks || [];
    const network = bundle.network || {};
    return (
      <div className="integration-block">
        <div className="integration-head">
          <div>
            <p>{bundle.provider_kind || "openai_compatible"}</p>
            <h2>{bundle.alias || "local.endpoint"}</h2>
          </div>
          {checkable && <button onClick={checkRunBundle}>Check</button>}
        </div>
        <div className="endpoint-line">
          <span>{bundle.preferred_base_url || bundle.base_url || "No endpoint"}</span>
          <button onClick={() => copyText("Endpoint", bundle.preferred_base_url || bundle.base_url)}>Copy endpoint</button>
        </div>
        {bundle.raw_runtime_base_url && (
          <div className="muted">Runtime fallback: {bundle.raw_runtime_base_url}</div>
        )}
        {network.mode && (
          <div className="network-line">
            <span>Network</span>
            <strong>{network.mode}</strong>
            <span>{network.bind_host}</span>
            <span>{network.auth}</span>
            <span>{network.advertise_host}</span>
          </div>
        )}
        <div className="copy-grid">
          <div>
            <div className="copy-title">
              <strong>Hermes</strong>
              <button onClick={() => copyText("Hermes config", hermesYaml)}>Copy</button>
            </div>
            <pre>{hermesYaml}</pre>
          </div>
          <div>
            <div className="copy-title">
              <strong>OpenClaw</strong>
              <button onClick={() => copyText("OpenClaw route", openclawYaml)}>Copy</button>
            </div>
            <pre>{openclawYaml}</pre>
          </div>
        </div>
        {checks.length > 0 && (
          <div className="checks">
            {checks.map((check) => (
              <div className={check.ok ? "check ok" : "check bad"} key={check.context}>
                <strong>{check.context}</strong>
                <span>{check.ok ? `ok ${check.status || ""}` : check.error || "unreachable"}</span>
              </div>
            ))}
            <div className="muted">{bundle.connectivity_summary?.message}</div>
          </div>
        )}
      </div>
    );
  }

  function MoeLaunchCards() {
    if (!moeCards.length) {
      return null;
    }
    return (
      <section className="moe-launch-section">
        <div className="section-head">
          <div>
            <p>MoE Run Anyway</p>
            <h2>Runtime Test Launch Cards</h2>
          </div>
          <span>{moeCards[0]?.moe_root?.available ? "checkout found" : "checkout missing"}</span>
        </div>
        <div className="launch-card-grid">
          {moeCards.map((card) => {
            const result = moeCardResults[card.card_id];
            const isRunnerCard = card.execution_mode === "runner";
            const isManualCard = card.execution_mode === "manual_evidence";
            const checkoutReady = !card.requires_moe_checkout || card.moe_root?.available;
            const smokeCommand = card.smoke_command?.shell_command || "";
            const launchCommand = card.launch_command?.shell_command || "";
            return (
              <div className="launch-card" key={card.card_id}>
                <div className="launch-card-title">
                  <div>
                    <p>{card.backend_family} - {card.card_type}</p>
                    <h2>{card.title}</h2>
                  </div>
                  <strong>{card.model_class}</strong>
                </div>
                <div className="launch-meta">
                  <span>{card.model}</span>
                  <span>{card.base_url}</span>
                  <span>{card.evidence_level}</span>
                  <span>{card.probe_tier}</span>
                  <span>{card.target_class}</span>
                  <span>{card.runtime_stack}</span>
                  <span>{card.execution_mode}</span>
                  {card.profile_id && <span>Profile: {card.profile_id}</span>}
                  {isRunnerCard && <span>{card.max_prompts} prompt / {card.repeats} repeat</span>}
                </div>
                <div className="launch-purpose">{card.purpose}</div>
                <div className="launch-purpose">{card.hardware_note}</div>
                {!!card.prerequisites?.length && (
                  <div className="launch-list">
                    <strong>Prereqs</strong>
                    {card.prerequisites.map((item) => <span key={item}>{item}</span>)}
                  </div>
                )}
                {!!card.expected_artifacts?.length && (
                  <div className="launch-list">
                    <strong>Artifacts</strong>
                    {card.expected_artifacts.map((item) => <span key={item}>{item}</span>)}
                  </div>
                )}
                {!!card.evidence_limits?.length && (
                  <div className="launch-list">
                    <strong>Limits</strong>
                    {card.evidence_limits.map((item) => <span key={item}>{item}</span>)}
                  </div>
                )}
                <div className="launch-actions">
                  {isRunnerCard && (
                    <button disabled={!checkoutReady} onClick={() => runMoeCard(card.card_id, "preflight")}>
                      Preflight
                    </button>
                  )}
                  {isRunnerCard && (
                    <button disabled={!checkoutReady} onClick={() => runMoeCard(card.card_id, "smoke")}>
                      Smoke
                    </button>
                  )}
                  {isManualCard && (
                    <button onClick={() => openManualEvidence(card)}>
                      Record evidence
                    </button>
                  )}
                  <button disabled={!launchCommand} onClick={() => copyText(`${card.title} launch`, launchCommand)}>
                    Copy launch
                  </button>
                  <button disabled={!smokeCommand} onClick={() => copyText(`${card.title} command`, smokeCommand)}>
                    Copy probe
                  </button>
                </div>
                <div className="launch-status">
                  <span>
                    {isRunnerCard
                      ? checkoutReady ? card.moe_root.path : `Set ${card.moe_root?.env_var || "MOE_RUN_ANYWAY_ROOT"}`
                      : `${card.target_class} - ${card.output_dir}`}
                  </span>
                  {result && (
                    <strong className={result.ok === false ? "bad-text" : result.ok === true ? "ok-text" : ""}>
                      {result.status}
                      {typeof result.returncode === "number" ? ` rc=${result.returncode}` : ""}
                    </strong>
                  )}
                </div>
                {result?.parsed_stdout?.run_dir && (
                  <div className="launch-artifact">{result.parsed_stdout.run_dir}</div>
                )}
                {result?.run_dir && (
                  <div className="launch-artifact">{result.run_dir}</div>
                )}
                {result?.stderr && <pre className="small-pre">{result.stderr}</pre>}
              </div>
            );
          })}
        </div>
      </section>
    );
  }

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
      <MoeLaunchCards />
      {manualEvidenceCard && (
        <div className="modal-backdrop" role="presentation">
          <form className="modal wide-modal" onSubmit={recordManualEvidence}>
            <div className="modal-title">
              <div>
                <p>{manualEvidenceCard.target_class}</p>
                <h2>{manualEvidenceCard.title}</h2>
              </div>
              <button type="button" onClick={closeManualEvidence}>Close</button>
            </div>
            <label className="field">
              <span>Evidence JSON</span>
              <textarea
                value={manualEvidenceInput}
                onChange={(event) => setManualEvidenceInput(event.target.value)}
                spellCheck="false"
              />
            </label>
            <label className="field">
              <span>Notes</span>
              <input
                value={manualEvidenceNotes}
                onChange={(event) => setManualEvidenceNotes(event.target.value)}
                spellCheck="false"
              />
            </label>
            {manualEvidenceMessage && <div className="secret-message">{manualEvidenceMessage}</div>}
            <div className="actions">
              <button type="submit">Record</button>
              <button type="button" onClick={() => copyText("Evidence JSON", manualEvidenceInput)}>Copy JSON</button>
            </div>
          </form>
        </div>
      )}
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
              <span>{profile.backend} - {profile.host_port} - {profile.network?.mode || "private_trusted_lan"}</span>
              <small>{profile.network?.bind_host || "0.0.0.0"} - {profile.network?.advertise_host || "local"} - {profile.warnings} warnings - {profile.errors} errors</small>
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
          <h2>Integration Preview</h2>
          <HarnessBundle bundle={integrationPreview} checkable={false} />
          {copyMessage && <div className="secret-message">{copyMessage}</div>}
          <h2>Recent Runs</h2>
          <div className="run-list">
            {runs.map((run) => (
              <button
                className={run.run_id === selectedRun ? "run-row active" : "run-row"}
                key={run.run_id}
                onClick={() => inspectRun(run.run_id)}
              >
                <strong>{run.profile_name || run.profile_id}</strong>
                <span>{run.status} - {run.client_base_url || run.base_url || run.health_url}</span>
              </button>
            ))}
          </div>
          <h2>Harness Bundle</h2>
          <HarnessBundle bundle={runBundle} checkable={true} />
        </section>
      </div>
    </main>
  );
}

createRoot(document.getElementById("root")).render(<App />);
