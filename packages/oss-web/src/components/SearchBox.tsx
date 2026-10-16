import { useEffect, useId, useState, type KeyboardEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { suggestTerm, suggestionQuery } from "../searchState";

/**
 * The search input with title suggestions (`GET /api/suggest`): an ARIA combobox. Arrow keys
 * move through the list, Enter picks the highlighted title (or submits what was typed), Escape
 * closes the list.
 */
export function SearchBox({
  value,
  onChange,
  onSubmit,
  collection,
  placeholder,
}: {
  value: string;
  onChange: (value: string) => void;
  onSubmit: (value: string) => void;
  collection?: string;
  placeholder?: string;
}) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const [term, setTerm] = useState("");
  useEffect(() => {
    const t = window.setTimeout(() => setTerm(suggestTerm(value)), 150);
    return () => window.clearTimeout(t);
  }, [value]);
  const suggest = useQuery({
    queryKey: ["suggest", term, collection ?? ""],
    queryFn: () => api.suggest(term, collection),
    enabled: open && term.length > 0,
    staleTime: 30_000,
  });
  const items = open && term ? (suggest.data?.suggestions ?? []) : [];

  function pick(title: string) {
    const q = suggestionQuery(title);
    onChange(q);
    setOpen(false);
    setActive(-1);
    onSubmit(q);
  }

  function onKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "ArrowDown" && items.length) {
      e.preventDefault();
      setOpen(true);
      setActive((i) => (i + 1) % items.length);
    } else if (e.key === "ArrowUp" && items.length) {
      e.preventDefault();
      setActive((i) => (i <= 0 ? items.length - 1 : i - 1));
    } else if (e.key === "Enter" && active >= 0 && items[active]) {
      e.preventDefault();
      pick(items[active]);
    } else if (e.key === "Enter") {
      setOpen(false); // the form submits what was typed
    } else if (e.key === "Escape") {
      setOpen(false);
      setActive(-1);
    }
  }

  return (
    <div className="relative flex-1">
      <input
        role="combobox"
        aria-label="Search"
        aria-expanded={items.length > 0}
        aria-controls={`${id}-list`}
        aria-autocomplete="list"
        aria-activedescendant={active >= 0 ? `${id}-${active}` : undefined}
        value={value}
        onChange={(e) => {
          onChange(e.target.value);
          setOpen(true);
          setActive(-1);
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => window.setTimeout(() => setOpen(false), 120)}
        onKeyDown={onKeyDown}
        placeholder={placeholder}
        className="w-full rounded border border-zinc-300 bg-white px-3 py-2 dark:border-zinc-700 dark:bg-zinc-900"
      />
      {items.length > 0 && (
        <ul
          id={`${id}-list`}
          role="listbox"
          aria-label="Suggested titles"
          className="absolute z-20 mt-1 max-h-72 w-full overflow-auto rounded border border-zinc-200 bg-white py-1 text-sm shadow-lg dark:border-zinc-700 dark:bg-zinc-900"
        >
          {items.map((title, i) => (
            <li
              key={title}
              id={`${id}-${i}`}
              role="option"
              aria-selected={i === active}
              onMouseDown={(e) => {
                e.preventDefault(); // keep the focus in the input
                pick(title);
              }}
              onMouseEnter={() => setActive(i)}
              className={`cursor-pointer truncate px-3 py-1 ${i === active ? "bg-blue-100 dark:bg-blue-900/40" : ""}`}
            >
              {title}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
