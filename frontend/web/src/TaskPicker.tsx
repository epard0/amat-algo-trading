import { useEffect, useId, useMemo, useRef, useState } from "react";
import { ChevronDown, Search } from "lucide-react";

export type PickerOption = { value: string; label: string; description?: string; group?: string; disabled?: boolean };
export function TaskPicker({ id, label, value, options, onChange, placeholder = "Search tasks…", disabled = false }: {
  id?: string; label: string; value: string; options: PickerOption[];
  onChange: (value: string) => void; placeholder?: string; disabled?: boolean;
}) {
  const generated = useId();
  const inputId = id || generated;
  const listId = `${inputId}-options`;
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const root = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const chosen = options.find((option) => option.value === value);
  const filtered = useMemo(() => options.filter((option) => `${option.label} ${option.description || ""} ${option.group || ""} ${option.value}`.toLowerCase().includes(query.toLowerCase())), [options, query]);
  // Parent status/clock refreshes recreate options arrays; keep keyboard position
  // unless the searchable option identities or availability actually change.
  const optionSet = JSON.stringify(filtered.map(option => [option.value, Boolean(option.disabled)]));
  useEffect(() => { setActive(Math.max(0, filtered.findIndex((option) => !option.disabled))); }, [query, optionSet]);
  useEffect(() => {
    if (!open) return;
    const dismiss = (event: MouseEvent) => { if (!root.current?.contains(event.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", dismiss);
    return () => document.removeEventListener("mousedown", dismiss);
  }, [open]);
  useEffect(() => { if (open) document.getElementById(`${listId}-${active}`)?.scrollIntoView({ block: "nearest" }); }, [active, open, listId]);
  const pick = (option?: PickerOption) => {
    if (disabled || !option || option.disabled) return;
    onChange(option.value); setOpen(false); setQuery(""); input.current?.focus();
  };
  const move = (direction: number) => {
    if (!filtered.length) return;
    let index = active;
    for (let count = 0; count < filtered.length; count++) {
      index = (index + direction + filtered.length) % filtered.length;
      if (!filtered[index].disabled) { setActive(index); break; }
    }
  };
  return <div className="task-picker" ref={root} onBlur={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false); }}>
    <label htmlFor={inputId}>{label}</label>
    <div className="task-picker-field" onClick={() => { if (!disabled) { input.current?.focus(); if (!open) { setOpen(true); setQuery(""); } } }}>
      <Search size={16} aria-hidden="true" />
      <input id={inputId} ref={input} disabled={disabled} role="combobox" aria-autocomplete="list" aria-expanded={open} aria-controls={listId}
        aria-activedescendant={open && filtered[active] ? `${listId}-${active}` : undefined}
        value={open ? query : chosen?.label || ""} placeholder={placeholder}
        onFocus={() => { setOpen(true); setQuery(""); }}
        onChange={(event) => { setQuery(event.target.value); setOpen(true); }}
        onKeyDown={(event) => {
          if (event.key === "ArrowDown" || event.key === "ArrowUp") { event.preventDefault(); if (!open) setOpen(true); else move(event.key === "ArrowDown" ? 1 : -1); }
          if (event.key === "Enter") { event.preventDefault(); if (open) pick(filtered[active]); else setOpen(true); }
          if (event.key === "Escape") { event.preventDefault(); setOpen(false); setQuery(""); }
          if (event.key === "Home" && open) { event.preventDefault(); setActive(Math.max(0, filtered.findIndex(option => !option.disabled))); }
          if (event.key === "End" && open) { event.preventDefault(); setActive(Math.max(0, filtered.map(option => !option.disabled).lastIndexOf(true))); }
        }} />
      <ChevronDown size={16} aria-hidden="true" />
    </div>
    {open && <div className="task-picker-options" id={listId} role="listbox" aria-label={label}>
      {filtered.length ? filtered.map((option, index) => <div key={option.value} id={`${listId}-${index}`} role="option"
        aria-selected={option.value === value} aria-disabled={option.disabled || undefined}
        className={index === active ? "active" : ""} onMouseDown={(event) => event.preventDefault()}
        onMouseEnter={() => setActive(index)} onClick={() => pick(option)}>
        {option.group && <small>{option.group}</small>}<strong>{option.label}</strong>
        {option.description && <span>{option.description}</span>}
      </div>) : <p className="muted">No matching tasks</p>}
    </div>}
  </div>;
}
