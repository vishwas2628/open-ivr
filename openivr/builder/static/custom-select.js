function initCustomDropdown(selectEl, config = {}) {
  if (!selectEl) return null;
  if (selectEl._customDropdown) {
    if (config && Object.keys(config).length) {
      Object.assign(selectEl._customDropdown.config, config);
    }
    selectEl._customDropdown.sync();
    return selectEl._customDropdown;
  }

  const effectiveConfig = Object.assign({}, config);
  if (!effectiveConfig.searchPlaceholder && selectEl.dataset.searchPlaceholder) {
    effectiveConfig.searchPlaceholder = selectEl.dataset.searchPlaceholder;
  }
  if (effectiveConfig.searchable === undefined && selectEl.dataset.searchable) {
    effectiveConfig.searchable = selectEl.dataset.searchable !== 'false';
  }

  selectEl.classList.add('custom-select-hidden');

  const wrapper = document.createElement('div');
  wrapper.className = 'custom-dropdown' + (selectEl.classList.contains('form-select-sm') ? ' custom-dropdown-sm' : '');
  wrapper.classList.toggle('hidden', selectEl.classList.contains('hidden'));

  const trigger = document.createElement('button');
  trigger.type = 'button';
  trigger.className = 'custom-dropdown-trigger';
  trigger.setAttribute('aria-haspopup', 'listbox');
  trigger.setAttribute('aria-expanded', 'false');

  const labelSpan = document.createElement('span');
  labelSpan.className = 'custom-dropdown-label';
  trigger.appendChild(labelSpan);

  const arrow = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  arrow.setAttribute('class', 'arrow-icon');
  arrow.setAttribute('viewBox', '0 0 20 20');
  arrow.setAttribute('fill', 'none');
  arrow.setAttribute('stroke', 'currentColor');
  const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
  path.setAttribute('stroke-linecap', 'round');
  path.setAttribute('stroke-linejoin', 'round');
  path.setAttribute('stroke-width', '2.5');
  path.setAttribute('d', 'M19 9l-7 7-7-7');
  arrow.appendChild(path);
  trigger.appendChild(arrow);

  const menu = document.createElement('div');
  menu.className = 'custom-dropdown-menu';
  menu.setAttribute('role', 'listbox');

  const searchWrap = document.createElement('div');
  searchWrap.className = 'custom-dropdown-search-wrap';
  const searchInput = document.createElement('input');
  searchInput.type = 'text';
  searchInput.className = 'custom-dropdown-search';
  searchInput.placeholder = effectiveConfig.searchPlaceholder || 'Search...';
  searchInput.autocomplete = 'off';
  searchWrap.appendChild(searchInput);
  menu.appendChild(searchWrap);

  const optionsList = document.createElement('div');
  optionsList.className = 'custom-dropdown-options';
  menu.appendChild(optionsList);

  const emptyMsg = document.createElement('div');
  emptyMsg.className = 'custom-dropdown-empty hidden';
  emptyMsg.textContent = 'No matching options';
  menu.appendChild(emptyMsg);

  wrapper.appendChild(trigger);
  wrapper.appendChild(menu);

  selectEl.parentNode.insertBefore(wrapper, selectEl.nextSibling);

  let highlightedIndex = -1;

  function sync() {
    optionsList.innerHTML = '';
    const opts = Array.from(selectEl.options);
    const isSearchable = effectiveConfig.searchable !== false && opts.length > 5;
    searchWrap.style.display = isSearchable ? '' : 'none';
    if (effectiveConfig.searchPlaceholder) {
      searchInput.placeholder = effectiveConfig.searchPlaceholder;
    }

    let selectedOpt = selectEl.selectedIndex >= 0 ? selectEl.options[selectEl.selectedIndex] : null;
    if (!selectedOpt && opts.length > 0) {
      selectedOpt = opts[0];
    }

    if (selectedOpt) {
      labelSpan.textContent = selectedOpt.textContent || selectedOpt.label;
      if (!selectedOpt.value) {
        labelSpan.classList.add('placeholder');
      } else {
        labelSpan.classList.remove('placeholder');
      }
    } else {
      labelSpan.textContent = effectiveConfig.placeholder || '-- Select --';
      labelSpan.classList.add('placeholder');
    }

    opts.forEach((opt, idx) => {
      const item = document.createElement('div');
      item.className = 'custom-dropdown-option' + (opt.selected ? ' selected' : '') + (opt.disabled ? ' disabled' : '');
      item.dataset.value = opt.value;
      item.dataset.index = idx;
      item.setAttribute('role', 'option');
      item.setAttribute('aria-selected', opt.selected ? 'true' : 'false');
      item.setAttribute('aria-disabled', opt.disabled ? 'true' : 'false');

      const text = document.createElement('span');
      text.className = 'option-text truncate';
      text.textContent = opt.textContent;
      item.appendChild(text);

      const check = document.createElement('span');
      check.className = 'check-icon';
      check.innerHTML = '&#10003;';
      item.appendChild(check);

      item.addEventListener('click', (e) => {
        e.stopPropagation();
        if (opt.disabled) return;
        selectValue(opt.value);
        closeMenu();
        trigger.focus();
      });

      optionsList.appendChild(item);
    });

    searchInput.value = '';
    filterOptions('');
  }

  function selectValue(val) {
    trigger.classList.remove('custom-dropdown-invalid');
    const err = document.getElementById('provider-error-msg');
    if (err) err.remove();
    if (selectEl.value !== val) {
      selectEl.value = val;
      selectEl.dispatchEvent(new Event('change', { bubbles: true }));
    }
    sync();
  }

  function filterOptions(query) {
    const q = (query || '').toLowerCase().trim();
    let visibleCount = 0;
    const items = optionsList.querySelectorAll('.custom-dropdown-option');
    items.forEach(it => {
      const match = !q || it.textContent.toLowerCase().includes(q);
      it.style.display = match ? 'flex' : 'none';
      if (match) visibleCount++;
      it.classList.remove('highlighted');
    });
    emptyMsg.classList.toggle('hidden', visibleCount > 0);
    highlightedIndex = -1;
  }

  function getVisibleItems() {
    return Array.from(optionsList.querySelectorAll('.custom-dropdown-option')).filter(it => it.style.display !== 'none');
  }

  function updateHighlight(items) {
    items.forEach((it, idx) => {
      it.classList.toggle('highlighted', idx === highlightedIndex);
      if (idx === highlightedIndex) {
        it.scrollIntoView({ block: 'nearest' });
      }
    });
  }

  function openMenu() {
    document.querySelectorAll('.custom-dropdown.open').forEach(dd => {
      if (dd !== wrapper) dd.classList.remove('open');
    });
    wrapper.classList.add('open');
    trigger.setAttribute('aria-expanded', 'true');
    searchInput.value = '';
    filterOptions('');

    const selectedItem = optionsList.querySelector('.custom-dropdown-option.selected');
    if (selectedItem) {
      selectedItem.scrollIntoView({ block: 'nearest' });
    }

    if (searchWrap.style.display !== 'none') {
      setTimeout(() => searchInput.focus(), 30);
    }
  }

  function closeMenu() {
    wrapper.classList.remove('open');
    trigger.setAttribute('aria-expanded', 'false');
    highlightedIndex = -1;
    const items = optionsList.querySelectorAll('.custom-dropdown-option');
    items.forEach(it => it.classList.remove('highlighted'));
  }

  function toggleMenu() {
    if (wrapper.classList.contains('open')) {
      closeMenu();
    } else {
      openMenu();
    }
  }

  trigger.addEventListener('click', (e) => {
    e.preventDefault();
    e.stopPropagation();
    toggleMenu();
  });

  searchInput.addEventListener('input', (e) => {
    filterOptions(e.target.value);
  });

  function handleKeyNav(e) {
    const visible = getVisibleItems();
    if (!visible.length) return;

    if (e.key === 'ArrowDown') {
      e.preventDefault();
      highlightedIndex = Math.min(highlightedIndex + 1, visible.length - 1);
      updateHighlight(visible);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      highlightedIndex = Math.max(highlightedIndex - 1, 0);
      updateHighlight(visible);
    } else if (e.key === 'Enter') {
      e.preventDefault();
      if (highlightedIndex >= 0 && visible[highlightedIndex]) {
        visible[highlightedIndex].click();
      } else if (visible.length === 1) {
        visible[0].click();
      }
    } else if (e.key === 'Escape') {
      e.preventDefault();
      closeMenu();
      trigger.focus();
    }
  }

  searchInput.addEventListener('keydown', handleKeyNav);

  trigger.addEventListener('keydown', (e) => {
    if (!wrapper.classList.contains('open')) {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp' || e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        openMenu();
      }
    } else {
      if (searchWrap.style.display === 'none') {
        handleKeyNav(e);
      } else if (e.key === 'Escape') {
        e.preventDefault();
        closeMenu();
      }
    }
  });

  if (selectEl.id) {
    const parentLabel = document.querySelector(`label[for="${selectEl.id}"]`);
    if (parentLabel) {
      parentLabel.style.cursor = 'pointer';
      parentLabel.addEventListener('click', (e) => {
        e.preventDefault();
        trigger.focus();
        toggleMenu();
      });
    }
  }

  sync();

  const instance = { sync, open: openMenu, close: closeMenu, wrapper, trigger, config: effectiveConfig };
  selectEl._customDropdown = instance;
  return instance;
}


window.initCustomDropdown = initCustomDropdown;
document.addEventListener('click', (e) => {
  if (!e.target.closest('.custom-dropdown')) {
    document.querySelectorAll('.custom-dropdown.open').forEach(dd => dd.classList.remove('open'));
  }
});

function initAllCustomSelects(root = document) {
  root.querySelectorAll('select.form-select, select.form-select-sm, select[data-custom-select]').forEach(selectEl => {
    if (selectEl.closest('.custom-select-hidden') || selectEl.classList.contains('custom-select-hidden')) {
      if (selectEl._customDropdown) selectEl._customDropdown.sync();
      return;
    }
    initCustomDropdown(selectEl);
  });
}

document.addEventListener('DOMContentLoaded', initAllCustomSelects);

// Auto-apply the trunk-style dropdown UI to any select added later (modals, dynamic rows)
const _customSelectObserver = new MutationObserver((mutations) => {
  for (const m of mutations) {
    m.addedNodes.forEach(node => {
      if (!node || node.nodeType !== 1) return;
      const selector = 'select.form-select, select.form-select-sm, select[data-custom-select]';
      if (node.matches && node.matches(selector) && !node._customDropdown) {
        initCustomDropdown(node);
      }
      if (node.querySelectorAll) {
        node.querySelectorAll(selector).forEach(sel => {
          if (!sel._customDropdown) initCustomDropdown(sel);
        });
      }
    });
  }
});
if (document.body) {
  _customSelectObserver.observe(document.body, { childList: true, subtree: true });
} else {
  document.addEventListener('DOMContentLoaded', () => {
    _customSelectObserver.observe(document.body, { childList: true, subtree: true });
  });
}
