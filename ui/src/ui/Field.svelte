<script lang="ts">
  import type { Snippet } from "svelte";

  // A settings row: label (and allowed range) on the left, the control on the right, help below.
  // `changed` shows the green dot used in the Canon web settings for edited, not-yet-applied values.
  let { label, range, help, changed = false, children }: {
    label: string; range?: string; help?: string; changed?: boolean; children: Snippet;
  } = $props();
</script>

<div class="field">
  <div class="label">{label}{#if range}<span class="range">{range}</span>{/if}</div>
  <div class="control">{@render children()}<span class="dot" class:show={changed}></span></div>
  {#if help}<div class="help">{help}</div>{/if}
</div>

<style>
  .field { display: grid; grid-template-columns: minmax(140px, 260px) 1fr; gap: 4px 16px; align-items: center;
           padding: 6px 0; }
  .label { color: var(--text); }
  .range { color: var(--dim); font-size: 11px; margin-left: 8px; }
  .control { display: flex; align-items: center; gap: 8px; min-width: 0; }
  .control :global(input:not([type="range"]):not([type="checkbox"])),
  .control :global(select) { flex: 1; min-width: 0; }
  .dot { width: 7px; height: 7px; border-radius: 50%; flex: none; }
  .dot.show { background: #2ecc71; }
  .help { grid-column: 2; font-size: 12px; color: var(--dim); }
  @media (max-width: 700px) {
    .field { grid-template-columns: 1fr; }
    .help { grid-column: 1; }
  }
</style>
