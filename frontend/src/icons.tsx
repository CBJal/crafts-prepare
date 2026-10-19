/** Original pictograms shared with the desktop stroke style. Not a remote icon set. */

const PATHS: Record<string, string> = {
  import: '<path d="M5 6.5h14v11H5z"/><path d="M8 6.5v11M12 10v5M10 12.5h4"/>',
  select: '<path d="M5 6.5h4M5 12h4M5 17.5h4"/><path d="M11 6.5l1.4 1.4L16 4.5"/><path d="M11 12l1.4 1.4L16 10"/><path d="M11 17.5l1.4 1.4L16 15.5"/>',
  clear: '<path d="M7 7l10 10M17 7L7 17"/>',
  export: '<path d="M12 14.5V5"/><path d="M8.5 8.2L12 4.8l3.5 3.4"/><path d="M5.5 15.5v3h13v-3"/>',
  gyro: '<path d="M7 8.5A5.5 5.5 0 0 1 16.2 7"/><path d="M14.2 4.8L16.4 7l-2.4 1.4"/><path d="M17 15.5A5.5 5.5 0 0 1 7.8 17"/><path d="M9.8 19.2L7.6 17l2.4-1.4"/>',
  cancel: '<path d="M8 8l8 8M16 8l-8 8"/><circle cx="12" cy="12" r="7.25"/>',
  retry: '<path d="M7 9.5A5 5 0 1 1 6.2 14"/><path d="M7 5.8v4h4"/>',
  tools: '<path d="M14.5 6.2a3 3 0 0 0-3.8 3.8L5.2 15.5l3.3 3.3 5.5-5.5a3 3 0 0 0 3.8-3.8L15.6 11l-2.6-2.6z"/>',
  save: '<path d="M5 4.5h11.2L19.5 8v11.5a1 1 0 0 1-1 1H5.5a1 1 0 0 1-1-1v-14a1 1 0 0 1 1-1z"/><path d="M8 4.5V9h7"/><path d="M8 20v-5.5h8V20"/>',
  open: '<path d="M3.5 8.5V6.8A1.3 1.3 0 0 1 4.8 5.5h4l1.6 1.8h8.8a1.3 1.3 0 0 1 1.3 1.3v9.1a1.3 1.3 0 0 1-1.3 1.3H4.8a1.3 1.3 0 0 1-1.3-1.3z"/>',
};

export function Icon({ name }: { name: keyof typeof PATHS }) {
  return (
    <svg className="icon" viewBox="0 0 24 24" aria-hidden="true">
      <g fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" dangerouslySetInnerHTML={{ __html: PATHS[name] }} />
    </svg>
  );
}
