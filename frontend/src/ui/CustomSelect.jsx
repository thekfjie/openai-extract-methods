import React, { useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Check, ChevronDown } from 'lucide-react';

const VIEWPORT_GAP = 8;
const MENU_GAP = 7;
const MAX_MENU_HEIGHT = 250;

function normalizeOptions(options) {
  return (Array.isArray(options) ? options : []).map((option) => {
    if (typeof option === 'string' || typeof option === 'number') {
      return { value: String(option), label: String(option), disabled: false };
    }
    return {
      ...option,
      value: String(option?.value ?? ''),
      label: option?.label ?? String(option?.value ?? ''),
      disabled: !!option?.disabled,
    };
  });
}

function enabledIndex(options, start, direction) {
  if (!options.length) return -1;
  for (let offset = 1; offset <= options.length; offset += 1) {
    const index = (start + direction * offset + options.length) % options.length;
    if (!options[index].disabled) return index;
  }
  return -1;
}

export default function CustomSelect({
  value,
  onChange,
  options = [],
  id,
  name,
  disabled = false,
  placeholder = '请选择',
  ariaLabel,
  className = '',
  style = {},
  compact = false,
}) {
  const generatedId = useId().replace(/:/g, '');
  const controlId = id || `automyai-select-${generatedId}`;
  const listboxId = `${controlId}-listbox`;
  const triggerRef = useRef(null);
  const menuRef = useRef(null);
  const normalized = useMemo(() => normalizeOptions(options), [options]);
  const selectedIndex = normalized.findIndex((option) => option.value === String(value ?? ''));
  const selectedOption = selectedIndex >= 0 ? normalized[selectedIndex] : null;
  const [isOpen, setIsOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(selectedIndex);
  const [placement, setPlacement] = useState('bottom');
  const [menuStyle, setMenuStyle] = useState({});

  const positionMenu = () => {
    const trigger = triggerRef.current;
    if (!trigger) return;
    const rect = trigger.getBoundingClientRect();
    const spaceBelow = window.innerHeight - rect.bottom - VIEWPORT_GAP - MENU_GAP;
    const spaceAbove = rect.top - VIEWPORT_GAP - MENU_GAP;
    const openAbove = spaceBelow < 150 && spaceAbove > spaceBelow;
    const available = Math.max(96, Math.min(MAX_MENU_HEIGHT, openAbove ? spaceAbove : spaceBelow));
    const width = Math.min(rect.width, window.innerWidth - VIEWPORT_GAP * 2);
    const left = Math.min(Math.max(VIEWPORT_GAP, rect.left), window.innerWidth - width - VIEWPORT_GAP);

    setPlacement(openAbove ? 'top' : 'bottom');
    setMenuStyle(openAbove
      ? { left, width, maxHeight: available, bottom: window.innerHeight - rect.top + MENU_GAP }
      : { left, width, maxHeight: available, top: rect.bottom + MENU_GAP });
  };

  useLayoutEffect(() => {
    if (!isOpen) return undefined;
    positionMenu();
    const reposition = () => positionMenu();
    window.addEventListener('resize', reposition);
    window.addEventListener('scroll', reposition, true);
    return () => {
      window.removeEventListener('resize', reposition);
      window.removeEventListener('scroll', reposition, true);
    };
  }, [isOpen]);

  useEffect(() => {
    if (!isOpen) return undefined;
    const closeOutside = (event) => {
      if (triggerRef.current?.contains(event.target) || menuRef.current?.contains(event.target)) return;
      setIsOpen(false);
    };
    const closeOnEscape = (event) => {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      setIsOpen(false);
      triggerRef.current?.focus();
    };
    document.addEventListener('pointerdown', closeOutside);
    document.addEventListener('keydown', closeOnEscape);
    return () => {
      document.removeEventListener('pointerdown', closeOutside);
      document.removeEventListener('keydown', closeOnEscape);
    };
  }, [isOpen]);

  useEffect(() => {
    if (!isOpen) return;
    const next = selectedIndex >= 0 && !normalized[selectedIndex]?.disabled
      ? selectedIndex
      : normalized.findIndex((option) => !option.disabled);
    setActiveIndex(next);
  }, [isOpen, selectedIndex, normalized]);

  useEffect(() => {
    if (!isOpen || activeIndex < 0) return;
    menuRef.current?.querySelector(`[data-option-index="${activeIndex}"]`)?.scrollIntoView({ block: 'nearest' });
  }, [activeIndex, isOpen]);

  const open = () => {
    if (disabled) return;
    setIsOpen(true);
  };

  const choose = (option) => {
    if (!option || option.disabled) return;
    onChange?.(option.value);
    setIsOpen(false);
    window.requestAnimationFrame(() => triggerRef.current?.focus());
  };

  const handleTriggerKeyDown = (event) => {
    if (disabled) return;
    if (!isOpen && ['ArrowDown', 'ArrowUp', 'Enter', ' '].includes(event.key)) {
      event.preventDefault();
      open();
      return;
    }
    if (!isOpen) return;
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      setActiveIndex((current) => enabledIndex(normalized, current < 0 ? -1 : current, 1));
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      setActiveIndex((current) => enabledIndex(normalized, current < 0 ? 0 : current, -1));
    } else if (event.key === 'Home') {
      event.preventDefault();
      setActiveIndex(normalized.findIndex((option) => !option.disabled));
    } else if (event.key === 'End') {
      event.preventDefault();
      setActiveIndex([...normalized].map((option, index) => ({ option, index })).reverse().find(({ option }) => !option.disabled)?.index ?? -1);
    } else if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      choose(normalized[activeIndex]);
    } else if (event.key === 'Tab') {
      setIsOpen(false);
    }
  };

  const menu = isOpen && typeof document !== 'undefined' ? createPortal(
    <div
      ref={menuRef}
      id={listboxId}
      role="listbox"
      aria-label={ariaLabel}
      className="custom-select-menu"
      data-placement={placement}
      style={menuStyle}
    >
      {normalized.map((option, index) => {
        const selected = index === selectedIndex;
        const active = index === activeIndex;
        return (
          <button
            type="button"
            role="option"
            aria-selected={selected}
            aria-disabled={option.disabled || undefined}
            disabled={option.disabled}
            id={`${listboxId}-option-${index}`}
            data-option-index={index}
            className={`custom-select-option ${selected ? 'selected' : ''} ${active ? 'active' : ''}`}
            key={`${option.value}-${index}`}
            onPointerMove={() => !option.disabled && setActiveIndex(index)}
            onMouseDown={(event) => event.preventDefault()}
            onClick={() => choose(option)}
          >
            <span>{option.label}</span>
            {selected ? <Check size={15} /> : null}
          </button>
        );
      })}
      {!normalized.length ? <div className="custom-select-empty">暂无选项</div> : null}
    </div>,
    document.body,
  ) : null;

  return (
    <div className={`custom-select ${isOpen ? 'open' : ''} ${compact ? 'compact' : ''} ${className}`} style={style}>
      {name ? <input type="hidden" name={name} value={String(value ?? '')} /> : null}
      <button
        ref={triggerRef}
        type="button"
        id={controlId}
        className="custom-select-trigger"
        aria-label={ariaLabel}
        aria-haspopup="listbox"
        aria-expanded={isOpen}
        aria-controls={isOpen ? listboxId : undefined}
        aria-activedescendant={isOpen && activeIndex >= 0 ? `${listboxId}-option-${activeIndex}` : undefined}
        disabled={disabled}
        onClick={() => setIsOpen((current) => !current)}
        onKeyDown={handleTriggerKeyDown}
      >
        <span className={`custom-select-value ${selectedOption ? '' : 'placeholder'}`}>{selectedOption?.label ?? placeholder}</span>
        <ChevronDown className="custom-select-chevron" size={16} />
      </button>
      {menu}
    </div>
  );
}
