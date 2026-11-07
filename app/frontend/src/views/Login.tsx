import { useState } from "react";

import { api } from "../api";
import { Icon } from "../components/Icon";
import { Spinner } from "../components/States";

export function Login({ onLogin }: { onLogin: (user: string) => void }) {
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      const r = await api.post<{ user: string }>("api/auth/login", { username, password });
      onLogin(r.user);
    } catch (ex) {
      setErr((ex as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login">
      <div className="login-art" aria-hidden>
        <div className="login-art-inner">
          <div className="brand-mark brand-mark-lg"><Logo /></div>
          <h2>Sorghum Protein Intelligence</h2>
          <p>
            Ask the lab's literature on kafirins, digestibility and grain quality. Explore the knowledge graph built from
            it. Every answer leads back to the page it came from.
          </p>
          <ul>
            <li><Icon name="chat" size={14} /> Cited answers from the library</li>
            <li><Icon name="graph" size={14} /> Genes, traits and processes, connected</li>
            <li><Icon name="file" size={14} /> The original page, with the evidence outlined</li>
          </ul>
        </div>
      </div>
      <form className="login-card" onSubmit={submit}>
        <h1>Sign in</h1>
        <p className="muted small">Shakoor Lab, Donald Danforth Plant Science Center</p>
        <label>
          <span>Username</span>
          <input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" required />
        </label>
        <label>
          <span>Password</span>
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" autoFocus required />
        </label>
        {err ? <div className="state state-error small" role="alert">{err}</div> : null}
        <button type="submit" className="btn btn-primary btn-block" disabled={busy}>
          {busy ? <Spinner size={14} /> : null} Sign in
        </button>
      </form>
    </div>
  );
}

export function Logo({ size = 20 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden>
      <g fill="currentColor">
        <ellipse cx="16" cy="7" rx="3.2" ry="4.2" />
        <ellipse cx="10.5" cy="13.5" rx="3.2" ry="4.2" />
        <ellipse cx="21.5" cy="13.5" rx="3.2" ry="4.2" />
        <ellipse cx="16" cy="19.5" rx="3.2" ry="4.2" />
        <rect x="15" y="22.5" width="2" height="8" rx="1" />
      </g>
    </svg>
  );
}
