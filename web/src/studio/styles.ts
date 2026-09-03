export const STUDIO_CSS = `
[data-character-robot-studio] {
  --crs-ink: #182421;
  --crs-muted: #63706d;
  --crs-paper: #f4f0e8;
  --crs-card: rgba(255, 255, 255, .78);
  --crs-line: rgba(27, 49, 43, .15);
  --crs-mint: #9fe0cd;
  --crs-teal: #155e5a;
  --crs-coral: #ef765f;
  --crs-gold: #efbd5b;
  min-height: 100vh;
  color: var(--crs-ink);
  background:
    radial-gradient(circle at 13% 12%, rgba(159, 224, 205, .55), transparent 28rem),
    radial-gradient(circle at 92% 2%, rgba(239, 189, 91, .3), transparent 22rem),
    linear-gradient(155deg, #f8f5ef 0%, #ece8df 100%);
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  line-height: 1.45;
}

[data-character-robot-studio] *,
[data-character-robot-studio] *::before,
[data-character-robot-studio] *::after { box-sizing: border-box; }

[data-character-robot-studio] button { font: inherit; }

[data-character-robot-studio] .crs-shell {
  width: min(1560px, 100%);
  margin: 0 auto;
  padding: clamp(18px, 3vw, 42px);
}

[data-character-robot-studio] .crs-header {
  display: flex;
  align-items: end;
  justify-content: space-between;
  gap: 24px;
  margin-bottom: 22px;
}

[data-character-robot-studio] .crs-kicker,
[data-character-robot-studio] .crs-section-label {
  margin: 0;
  color: var(--crs-teal);
  font: 700 11px/1.2 ui-monospace, SFMono-Regular, Menlo, monospace;
  letter-spacing: .14em;
  text-transform: uppercase;
}

[data-character-robot-studio] .crs-header h1 {
  margin: 5px 0 0;
  font: 750 clamp(28px, 4.2vw, 58px)/.98 Georgia, "Times New Roman", serif;
  letter-spacing: -.045em;
}

[data-character-robot-studio] .crs-header h1 em {
  color: var(--crs-coral);
  font-weight: 500;
}

[data-character-robot-studio] .crs-top-status {
  display: flex;
  align-items: center;
  gap: 9px;
  flex-wrap: wrap;
  justify-content: flex-end;
}

[data-character-robot-studio] .crs-pill,
[data-character-robot-studio] .crs-evidence-badge {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  min-height: 30px;
  padding: 6px 10px;
  border: 1px solid var(--crs-line);
  border-radius: 999px;
  background: rgba(255, 255, 255, .68);
  color: var(--crs-muted);
  font: 700 10px/1 ui-monospace, SFMono-Regular, Menlo, monospace;
  letter-spacing: .06em;
  text-transform: uppercase;
}

[data-character-robot-studio] .crs-pill::before,
[data-character-robot-studio] .crs-evidence-badge::before {
  content: "";
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #9ca8a5;
}

[data-character-robot-studio] .crs-pill.ready::before { background: #23a978; box-shadow: 0 0 0 4px rgba(35, 169, 120, .12); }
[data-character-robot-studio] .crs-pill.error::before { background: var(--crs-coral); }
[data-character-robot-studio] .crs-evidence-badge.concept_only::before { background: var(--crs-coral); }
[data-character-robot-studio] .crs-evidence-badge.digital_checks_passed::before { background: var(--crs-gold); }
[data-character-robot-studio] .crs-evidence-badge.within_qualified_profile::before,
[data-character-robot-studio] .crs-evidence-badge.exact_build_verified::before { background: #23a978; }

[data-character-robot-studio] .crs-layout {
  display: grid;
  grid-template-columns: minmax(0, 1.72fr) minmax(310px, .82fr);
  gap: 16px;
  align-items: start;
}

[data-character-robot-studio] .crs-panel {
  overflow: hidden;
  border: 1px solid var(--crs-line);
  border-radius: 19px;
  background: var(--crs-card);
  box-shadow: 0 18px 50px rgba(34, 47, 43, .08);
  backdrop-filter: blur(18px);
}

[data-character-robot-studio] .crs-panel-head {
  display: flex;
  justify-content: space-between;
  gap: 14px;
  align-items: center;
  padding: 14px 16px;
  border-bottom: 1px solid var(--crs-line);
}

[data-character-robot-studio] .crs-panel-head strong {
  display: block;
  margin-top: 2px;
  font-size: 14px;
}

[data-character-robot-studio] .crs-stage {
  position: relative;
  min-height: clamp(410px, 59vh, 720px);
  background: #111619;
}

[data-character-robot-studio] .crs-stage canvas { display: block; width: 100%; height: 100%; }

[data-character-robot-studio] .crs-view-state {
  position: absolute;
  inset: 50% auto auto 50%;
  z-index: 2;
  width: min(360px, calc(100% - 40px));
  transform: translate(-50%, -50%);
  color: #bdc7c4;
  text-align: center;
  pointer-events: none;
}

[data-character-robot-studio] .crs-view-state strong { display: block; color: #fff; font-size: 16px; margin-bottom: 5px; }
[data-character-robot-studio] .crs-view-state[hidden] { display: none; }

[data-character-robot-studio] .crs-selection {
  position: absolute;
  top: 14px;
  left: 14px;
  z-index: 3;
  max-width: calc(100% - 28px);
  padding: 8px 11px;
  border: 1px solid rgba(255, 255, 255, .18);
  border-radius: 10px;
  background: rgba(12, 18, 20, .8);
  color: #dbe4e1;
  font: 650 11px/1.25 ui-monospace, SFMono-Regular, Menlo, monospace;
  backdrop-filter: blur(10px);
}

[data-character-robot-studio] .crs-stage-footer {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 14px;
  align-items: center;
  padding: 13px 16px 15px;
  border-top: 1px solid var(--crs-line);
}

[data-character-robot-studio] .crs-scenario-buttons { display: flex; flex-wrap: wrap; gap: 7px; }

[data-character-robot-studio] .crs-scenario-button,
[data-character-robot-studio] .crs-part-button {
  border: 1px solid var(--crs-line);
  border-radius: 9px;
  background: rgba(255, 255, 255, .68);
  color: var(--crs-ink);
  cursor: pointer;
  transition: transform .15s ease, border-color .15s ease, background .15s ease;
}

[data-character-robot-studio] .crs-scenario-button { padding: 8px 10px; font-size: 11px; font-weight: 750; text-transform: capitalize; }
[data-character-robot-studio] .crs-scenario-button:hover,
[data-character-robot-studio] .crs-part-button:hover { transform: translateY(-1px); border-color: rgba(21, 94, 90, .5); }
[data-character-robot-studio] .crs-scenario-button.active,
[data-character-robot-studio] .crs-part-button.active { border-color: var(--crs-teal); background: var(--crs-teal); color: #fff; }
[data-character-robot-studio] .crs-scenario-button:disabled { cursor: wait; opacity: .55; }

[data-character-robot-studio] .crs-timeline { min-width: 0; }
[data-character-robot-studio] .crs-timeline-track {
  --progress: 0%;
  position: relative;
  height: 6px;
  overflow: hidden;
  border-radius: 999px;
  background: #d9d8d1;
}
[data-character-robot-studio] .crs-timeline-track::before {
  content: "";
  position: absolute;
  inset: 0 auto 0 0;
  width: var(--progress);
  background: linear-gradient(90deg, var(--crs-teal), var(--crs-mint));
}
[data-character-robot-studio] .crs-timeline-meta {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  margin-top: 7px;
  color: var(--crs-muted);
  font: 600 10px/1.25 ui-monospace, SFMono-Regular, Menlo, monospace;
}

[data-character-robot-studio] .crs-sidebar { display: grid; gap: 16px; }
[data-character-robot-studio] .crs-section { padding: 16px; }
[data-character-robot-studio] .crs-section + .crs-section { border-top: 1px solid var(--crs-line); }
[data-character-robot-studio] .crs-section-title { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 12px; }
[data-character-robot-studio] .crs-section-title h2 { margin: 0; font: 750 16px/1.15 Georgia, "Times New Roman", serif; }

[data-character-robot-studio] .crs-draft-name { margin: 0; font: 750 24px/1.05 Georgia, "Times New Roman", serif; letter-spacing: -.02em; }
[data-character-robot-studio] .crs-draft-role { margin: 5px 0 11px; color: var(--crs-teal); font-size: 12px; font-weight: 750; }
[data-character-robot-studio] .crs-draft-brief { margin: 0; color: var(--crs-muted); font-size: 13px; }
[data-character-robot-studio] .crs-hash { display: block; overflow: hidden; margin-top: 12px; color: #7d8986; font: 10px/1.2 ui-monospace, SFMono-Regular, Menlo, monospace; text-overflow: ellipsis; white-space: nowrap; }

[data-character-robot-studio] .crs-profile-card {
  padding: 12px;
  border: 1px solid var(--crs-line);
  border-radius: 12px;
  background: rgba(244, 240, 232, .65);
}
[data-character-robot-studio] .crs-profile-card strong { display: block; font-size: 13px; }
[data-character-robot-studio] .crs-profile-id { display: block; margin: 3px 0 9px; color: var(--crs-muted); font: 10px/1.2 ui-monospace, SFMono-Regular, Menlo, monospace; }
[data-character-robot-studio] .crs-chip-list { display: flex; flex-wrap: wrap; gap: 5px; }
[data-character-robot-studio] .crs-chip { padding: 4px 7px; border-radius: 999px; background: #e2ebe7; color: #34534d; font-size: 10px; font-weight: 700; }
[data-character-robot-studio] .crs-measure { margin-top: 9px; color: var(--crs-muted); font-size: 11px; }

[data-character-robot-studio] .crs-part-list { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 7px; }
[data-character-robot-studio] .crs-part-button { min-width: 0; padding: 9px; text-align: left; }
[data-character-robot-studio] .crs-part-button strong,
[data-character-robot-studio] .crs-part-button span { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
[data-character-robot-studio] .crs-part-button strong { font-size: 11px; }
[data-character-robot-studio] .crs-part-button span { margin-top: 2px; font-size: 9px; opacity: .72; text-transform: uppercase; }

[data-character-robot-studio] .crs-warning-list { display: grid; gap: 7px; }
[data-character-robot-studio] .crs-warning {
  display: grid;
  grid-template-columns: 8px minmax(0, 1fr);
  gap: 9px;
  align-items: start;
  padding: 10px;
  border-radius: 10px;
  background: rgba(239, 189, 91, .14);
}
[data-character-robot-studio] .crs-warning::before { content: ""; width: 8px; height: 8px; margin-top: 4px; border-radius: 50%; background: var(--crs-gold); }
[data-character-robot-studio] .crs-warning.error { background: rgba(239, 118, 95, .12); }
[data-character-robot-studio] .crs-warning.error::before { background: var(--crs-coral); }
[data-character-robot-studio] .crs-warning.info::before { background: var(--crs-teal); }
[data-character-robot-studio] .crs-warning strong { display: block; font: 700 9px/1.2 ui-monospace, SFMono-Regular, Menlo, monospace; letter-spacing: .04em; }
[data-character-robot-studio] .crs-warning p { margin: 3px 0 0; color: var(--crs-muted); font-size: 11px; }
[data-character-robot-studio] .crs-warning .crs-warning-evidence { color: var(--crs-ink); font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
[data-character-robot-studio] .crs-warning .crs-warning-suggestion { color: var(--crs-teal); font-weight: 700; }
[data-character-robot-studio] .crs-empty { margin: 0; color: var(--crs-muted); font-size: 12px; }

[data-character-robot-studio] .crs-build-button {
  width: 100%;
  padding: 11px 13px;
  border: 1px solid var(--crs-teal);
  border-radius: 10px;
  background: var(--crs-teal);
  color: #fff;
  cursor: pointer;
  font-size: 12px;
  font-weight: 760;
}
[data-character-robot-studio] .crs-build-button:disabled { border-color: #aeb9b5; background: #aeb9b5; cursor: not-allowed; }
[data-character-robot-studio] .crs-import-button {
  display: block;
  margin-top: 7px;
  padding: 8px 10px;
  border: 1px solid var(--crs-line);
  border-radius: 9px;
  color: var(--crs-teal);
  cursor: pointer;
  font-size: 10px;
  font-weight: 700;
  text-align: center;
}
[data-character-robot-studio] .crs-import-button.busy { opacity: .55; pointer-events: none; }
[data-character-robot-studio] .crs-import-button input { display: none; }
[data-character-robot-studio] .crs-build-copy { margin: 9px 0 0; color: var(--crs-muted); font-size: 10px; }
[data-character-robot-studio] .crs-artifact-list { display: grid; gap: 6px; margin-top: 10px; }
[data-character-robot-studio] .crs-manifest {
  display: grid;
  gap: 4px;
  padding: 10px;
  border: 1px solid rgba(21, 94, 90, .28);
  border-radius: 9px;
  background: rgba(159, 224, 205, .14);
}
[data-character-robot-studio] .crs-manifest strong { font-size: 11px; }
[data-character-robot-studio] .crs-manifest span { color: var(--crs-muted); font-size: 9px; }
[data-character-robot-studio] .crs-manifest code,
[data-character-robot-studio] .crs-artifact code {
  overflow-wrap: anywhere;
  color: #53605d;
  font: 8px/1.35 ui-monospace, SFMono-Regular, Menlo, monospace;
}
[data-character-robot-studio] .crs-artifact {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 8px;
  align-items: center;
  padding: 8px 9px;
  border: 1px solid var(--crs-line);
  border-radius: 9px;
  color: var(--crs-ink);
  text-decoration: none;
}
[data-character-robot-studio] .crs-artifact:hover { border-color: var(--crs-teal); background: rgba(159, 224, 205, .18); }
[data-character-robot-studio] .crs-artifact-description { display: grid; min-width: 0; gap: 2px; }
[data-character-robot-studio] .crs-artifact strong,
[data-character-robot-studio] .crs-artifact-description > span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
[data-character-robot-studio] .crs-artifact strong { font-size: 10px; }
[data-character-robot-studio] .crs-artifact-description > span { color: var(--crs-muted); font: 9px/1 ui-monospace, SFMono-Regular, Menlo, monospace; text-transform: uppercase; }
[data-character-robot-studio] .crs-download-action { color: var(--crs-teal); font-size: 9px; font-weight: 800; text-transform: uppercase; }

[data-character-robot-studio] .crs-footnote {
  grid-column: 1 / -1;
  display: flex;
  justify-content: space-between;
  gap: 18px;
  padding: 4px 3px 0;
  color: var(--crs-muted);
  font-size: 10px;
}

@media (max-width: 900px) {
  [data-character-robot-studio] .crs-layout { grid-template-columns: 1fr; }
  [data-character-robot-studio] .crs-stage { min-height: 480px; }
  [data-character-robot-studio] .crs-footnote { grid-column: 1; }
}

@media (max-width: 620px) {
  [data-character-robot-studio] .crs-shell { padding: 16px; }
  [data-character-robot-studio] .crs-header { align-items: start; flex-direction: column; }
  [data-character-robot-studio] .crs-top-status { justify-content: flex-start; }
  [data-character-robot-studio] .crs-stage { min-height: 420px; }
  [data-character-robot-studio] .crs-stage-footer { grid-template-columns: 1fr; }
  [data-character-robot-studio] .crs-footnote { flex-direction: column; gap: 4px; }
}

@media (prefers-reduced-motion: reduce) {
  [data-character-robot-studio] *,
  [data-character-robot-studio] *::before,
  [data-character-robot-studio] *::after { scroll-behavior: auto !important; transition: none !important; }
}
`;

export function installStudioStyles(root: HTMLElement): void {
  const style = document.createElement("style");
  style.dataset.characterRobotStudioStyles = "";
  style.textContent = STUDIO_CSS;
  root.prepend(style);
}
