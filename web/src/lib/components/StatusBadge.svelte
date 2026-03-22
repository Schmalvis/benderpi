<script>
  export let status = 'unknown';
  export let label = '';

  const colors = {
    online: 'bg-success',
    offline: 'bg-error',
    unknown: 'bg-warning',
  };
</script>

<div class="flex items-center gap-2">
  <span class="inline-block w-2 h-2 rounded-full {colors[status] || colors.unknown}
               {status === 'online' ? 'animate-status-pulse' : ''}"></span>
  <span class="text-sm">{label || status}</span>
</div>
