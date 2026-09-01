import { TanaNode, NodeField } from '../types';

const MONTH_NAMES: Record<string, number> = {
  january: 1, jan: 1,
  february: 2, feb: 2,
  march: 3, mar: 3,
  april: 4, apr: 4,
  may: 5,
  june: 6, jun: 6,
  july: 7, jul: 7,
  august: 8, aug: 8,
  september: 9, sep: 9, sept: 9,
  october: 10, oct: 10,
  november: 11, nov: 11,
  december: 12, dec: 12
};

const DATE_FIELD_NAMES = new Set([
  'date', 'due date', 'event date', 'when', 'occurrence date',
  'original date', 'scheduled date', 'start date', 'target date'
]);

export function parseIsoDate(text?: string): string | null {
  if (!text) return null;
  // 1. Matches YYYY-MM-DD
  const m1 = text.match(/\b(19\d\d|20\d\d)-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])\b/);
  if (m1) return m1[0];

  // 2. Matches "September 1, 2019" or "Sep 1 2019"
  const monthsPat = Object.keys(MONTH_NAMES).join('|');
  const m2 = new RegExp(`\\b(${monthsPat})\\s+(\\d{1,2})(?:st|nd|rd|th)?(?:,)?\\s+(19\\d\\d|20\\d\\d)\\b`, 'i').exec(text);
  if (m2) {
    const month = MONTH_NAMES[m2[1].toLowerCase()] || 1;
    const day = parseInt(m2[2], 10);
    const yr = parseInt(m2[3], 10);
    return `${yr.toString().padStart(4, '0')}-${month.toString().padStart(2, '0')}-${day.toString().padStart(2, '0')}`;
  }

  // 3. Matches "1 September 2019"
  const m3 = new RegExp(`\\b(\\d{1,2})(?:st|nd|rd|th)?\\s+(${monthsPat})\\s+(19\\d\\d|20\\d\\d)\\b`, 'i').exec(text);
  if (m3) {
    const day = parseInt(m3[1], 10);
    const month = MONTH_NAMES[m3[2].toLowerCase()] || 1;
    const yr = parseInt(m3[3], 10);
    return `${yr.toString().padStart(4, '0')}-${month.toString().padStart(2, '0')}-${day.toString().padStart(2, '0')}`;
  }

  return null;
}

export function parseFieldDate(node: TanaNode): string | null {
  if (!node.fields) return null;
  for (const field of node.fields) {
    const fn = (field.field_name || '').toLowerCase().trim();
    if (DATE_FIELD_NAMES.has(fn) || fn.includes('date') || fn.includes('when')) {
      const parsed = parseIsoDate(field.value_text);
      if (parsed) return parsed;
    }
  }
  return null;
}

export function isDayNode(node: TanaNode): [boolean, string | null] {
  const tagNames = (node.supertags || []).map(t => t.tag_name.toLowerCase());
  const tagIds = (node.supertags || []).map(t => t.tag_id);
  const hasDayTag = tagNames.includes('day') || tagIds.includes('1Kcq0q_pf5Fn');

  const cleanName = (node.name || '').replace(/<[^>]+>/g, '').trim();
  const parsedDate = parseIsoDate(cleanName);
  if (parsedDate) return [true, parsedDate];

  if (hasDayTag) {
    const fDate = parseFieldDate(node);
    if (fDate) return [true, fDate];
    if (node.created_at) {
      const cDate = parseIsoDate(node.created_at);
      if (cDate) return [true, cDate];
    }
  }
  return [false, null];
}

export function resolveNodeProvenance(node: TanaNode, parentChain: TanaNode[]): Record<string, any> {
  const [selfIsDay, selfDayDate] = isDayNode(node);
  if (selfIsDay && selfDayDate) {
    return {
      effective_date: selfDayDate,
      date_source: 'day_node',
      date_source_node_id: node.id,
      calendar_distance: 0,
      calendar_path: selfDayDate,
      ancestor_day_node_id: node.id,
      ancestor_year_node_id: selfDayDate.substring(0, 4)
    };
  }

  let ancestorDayId: string | null = null;
  let ancestorDayDate: string | null = null;
  let calendarDistance: number | null = null;

  for (let i = 0; i < parentChain.length; i++) {
    const p = parentChain[i];
    const [pIsDay, pDate] = isDayNode(p);
    if (pIsDay && pDate && !ancestorDayId) {
      ancestorDayId = p.id;
      ancestorDayDate = pDate;
      calendarDistance = i + 1;
    }
  }

  const explicitFieldDate = parseFieldDate(node);

  if (ancestorDayDate) {
    return {
      effective_date: ancestorDayDate,
      date_source: 'day_node',
      date_source_node_id: ancestorDayId,
      calendar_distance: calendarDistance,
      calendar_path: ancestorDayDate,
      ancestor_day_node_id: ancestorDayId,
      ancestor_year_node_id: ancestorDayDate.substring(0, 4)
    };
  }

  if (explicitFieldDate) {
    return {
      effective_date: explicitFieldDate,
      date_source: 'field',
      date_source_node_id: node.id,
      calendar_distance: 0,
      calendar_path: explicitFieldDate,
      ancestor_day_node_id: null,
      ancestor_year_node_id: explicitFieldDate.substring(0, 4)
    };
  }

  if (node.created_at) {
    const cDate = parseIsoDate(node.created_at);
    if (cDate) {
      return {
        effective_date: cDate,
        date_source: 'fallback_created',
        date_source_node_id: node.id,
        calendar_distance: null,
        calendar_path: cDate,
        ancestor_day_node_id: null,
        ancestor_year_node_id: cDate.substring(0, 4)
      };
    }
  }

  return {
    effective_date: null,
    date_source: 'none',
    date_source_node_id: null,
    calendar_distance: null,
    calendar_path: null,
    ancestor_day_node_id: null,
    ancestor_year_node_id: null
  };
}

export function computeTemporalScore(
  nodeDateStr?: string,
  targetDateStr?: string,
  dateFromStr?: string,
  dateToStr?: string
): number {
  if (!nodeDateStr) return 0.0;

  const nTime = new Date(nodeDateStr.substring(0, 10)).getTime();
  if (isNaN(nTime)) return 0.0;

  if (dateFromStr) {
    const fTime = new Date(dateFromStr.substring(0, 10)).getTime();
    if (!isNaN(fTime) && nTime < fTime) return 0.0;
  }
  if (dateToStr) {
    const tTime = new Date(dateToStr.substring(0, 10)).getTime();
    if (!isNaN(tTime) && nTime > tTime) return 0.0;
  }

  if (!targetDateStr) return 1.0;

  const targetTime = new Date(targetDateStr.substring(0, 10)).getTime();
  if (isNaN(targetTime)) return 0.5;

  const deltaDays = Math.abs(Math.round((nTime - targetTime) / (1000 * 60 * 60 * 24)));

  if (deltaDays === 0) return 1.0;
  if (deltaDays === 1) return 0.95;
  if (deltaDays <= 3) return 0.85;
  if (deltaDays <= 7) return 0.70;
  if (deltaDays <= 14) return 0.50;
  if (deltaDays <= 30) return 0.25;
  return Math.max(0.0, 0.25 * Math.exp(-(deltaDays - 30) / 60.0));
}

export function classifyTemporalRelationship(
  node: TanaNode,
  targetDateStr?: string,
  targetYear?: string
): string {
  const fieldDate = parseFieldDate(node);
  if (fieldDate && node.effective_date && node.date_source === 'day_node') {
    if (fieldDate.substring(0, 4) !== node.effective_date.substring(0, 4)) {
      return 'conflicting';
    }
  }

  const fullText = `${node.name} ${node.description || ''}`.toLowerCase();
  const textYears: string[] = fullText.match(/\b(19\d\d|20\d\d)\b/g) || [];
  const refYear = targetYear || (targetDateStr ? targetDateStr.substring(0, 4) : null);

  if (node.effective_date && refYear) {
    const nodeYear = node.effective_date.substring(0, 4);
    if (nodeYear !== refYear && textYears.includes(refYear)) {
      return 'retrospective';
    }
    if (nodeYear === refYear) {
      return 'contemporaneous';
    }
  }

  if (!node.effective_date || node.date_source === 'none') {
    return 'undated';
  }

  return 'contemporaneous';
}

export function parseTemporalIntent(queryText: string, referenceDate?: Date): Record<string, any> {
  const ref = referenceDate || new Date();
  const q = queryText.trim().toLowerCase();

  const patterns = [
    /this time in (\d{4})/,
    /around (?:the\s+)?(\d{1,2}(?:st|nd|rd|th)?\s+)?([a-z]+)\s+(\d{4})/,
    /in ([a-z]+)\s+(\d{4})/,
    /during (\d{4})/,
    /back in (\d{4})/,
    /in (\d{4})/,
    /last year/,
    /(\d+)\s+years ago/,
    /around this date last year/,
    /what was i doing/,
    /what was happening/
  ];

  let hasIntent = false;
  for (const pat of patterns) {
    if (pat.test(q)) {
      hasIntent = true;
      break;
    }
  }

  let targetDate: string | null = null;
  let dateFrom: string | null = null;
  let dateTo: string | null = null;
  let temporalMode = 'none';

  if (hasIntent) {
    temporalMode = 'strict';
    const mThisTime = q.match(/(?:around\s+)?this time (?:in|of)\s+(\d{4})/);
    if (mThisTime) {
      const yr = parseInt(mThisTime[1], 10);
      const mm = (ref.getMonth() + 1).toString().padStart(2, '0');
      const dd = ref.getDate().toString().padStart(2, '0');
      targetDate = `${yr}-${mm}-${dd}`;
      dateFrom = `${yr}-${mm}-01`;
      dateTo = `${yr}-${mm}-28`;
    }
  }

  return {
    has_temporal_intent: hasIntent,
    target_date: targetDate,
    date_from: dateFrom,
    date_to: dateTo,
    temporal_mode: temporalMode
  };
}
