<script lang="ts">
  import { slots } from "../lib/slots.svelte";

  // The Initialization dialog: the selected App Settings (preset links kept) or all of them.
  let { onclose }: { onclose: () => void } = $props();
  let scope = $state<"slot" | "all">("slot");

  async function ok() {
    await slots.command({ type: "initialize", scope });
    onclose();
  }
</script>

<div class="backdrop" role="presentation" onclick={onclose}>
  <div class="dialog" role="dialog" aria-modal="true" aria-label="Initialization" tabindex="-1"
       onclick={(e) => e.stopPropagation()} onkeydown={(e) => e.key === "Escape" && onclose()}>
    <h2>Initialization</h2>
    <label><input type="radio" bind:group={scope} value="slot" /> Selected app settings</label>
    <label><input type="radio" bind:group={scope} value="all" /> All app settings (and the camera preset links)</label>
    <p class="dim">Settings go back to their defaults. Nothing is kept on disk until you press Save.</p>
    <div class="buttons">
      <button onclick={onclose}>Cancel</button>
      <button class="primary" onclick={ok}>OK</button>
    </div>
  </div>
</div>

<style>
  .backdrop { position: fixed; inset: 0; background: rgba(0, 0, 0, 0.55); display: grid; place-items: center; z-index: 20; }
  .dialog { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); padding: 16px 20px;
            width: min(420px, calc(100vw - 32px)); display: flex; flex-direction: column; gap: 8px; }
  h2 { margin: 0 0 4px; font-size: 16px; }
  label { display: flex; gap: 8px; align-items: center; min-height: 32px; }
  input[type="radio"] { min-height: 0; accent-color: var(--accent); }
  .buttons { display: flex; justify-content: flex-end; gap: 8px; margin-top: 6px; }
  .buttons button { min-width: 90px; }
  p { font-size: 12px; margin: 0; }
</style>
