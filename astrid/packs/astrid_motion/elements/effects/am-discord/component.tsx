import type {ReactElement} from 'react';
import {useCurrentFrame} from 'remotion';
import {
  type ElementComponentProps,
  narrowParams,
} from '../../../../rendering/elements/_shared/contracts';
import {
  COLOR,
  FAMILY,
  clamp,
  finiteNumber,
  integerIn,
  renderableFile,
} from '../../_shared/am';
import {
  type Token,
  dividerState,
  reactionCount,
  revealed,
  stampScale,
  tokenize,
  totalChars,
  underlineProgress,
  wordKey,
} from './discord-core';

// am-discord: a pixel-styled RECONSTRUCTION of a Discord channel window. It is
// not a screenshot and carries no Discord logo or wordmark. The window is 1280x720,
// centred on the plate, with a 2 px outline and a hard offset shadow. Messages
// stack from the bottom, joins and date dividers interleave in time order, and
// reactions, underline and strike decorations tick on whole frames.
// Discord palette: community values from the lore notes (04-lore.md B3), so
// they are [S]/[U] and should be checked against a real client before sign-off.

const PALETTE = {
  rail: '#1E1F22',
  side: '#2B2D31',
  chat: '#313338',
  input: '#383A40',
  selected: '#404249',
  rule: '#3F4147',
  text: '#F2F3F5',
  body: '#F2F3F5',
  muted: '#949BA4',
  icon: '#B5BAC1',
  blurple: '#5865F2',
  blurpleInk: '#FFFFFF',
  green: '#23A559',
  mention: 'rgba(88, 101, 242, 0.3)',
  mentionText: '#C9CDFB',
  rust: COLOR.rust,
  orange: COLOR.orange,
};

const MONO = `'${FAMILY.label}', monospace`;
const SANS = `'${FAMILY.body}', 'Noto Sans', sans-serif`;
const EMOJI = `'Apple Color Emoji', 'Noto Color Emoji', sans-serif`;

type MessageSpec = {
  author: string;
  tag: string;
  avatarColor: string;
  time: string;
  lines: string[];
  appearAt: number;
  typeOn: boolean;
};
type DividerSpec = {label: string; at: number; jump: boolean | null};
type ReactionSpec = {
  emoji: string;
  countFrom: number;
  countTo: number;
  startAt: number;
  stepFrames: number;
  onMessage: number | null;
  mine: boolean;
};
type JoinSpec = {name: string; at: number; time: string};
type MarkSpec = {word: string; at: number; color: string; onMessage: number | null};
type StrikeSpec = {word: string; at: number; onMessage: number | null};
type FrameSpec = {width: number; height: number; radius: number; outline: string; shadow: number};

type Params = {
  server?: string;
  channel?: string;
  channels?: string[];
  messages?: unknown[];
  dateDividers?: unknown[];
  reactions?: unknown[];
  joins?: unknown[];
  underline?: unknown[];
  strike?: unknown[];
  badge?: string;
  frame?: Partial<FrameSpec>;
  jumpFrames?: number;
  typeStepFrames?: number;
};

const obj = (value: unknown): Record<string, unknown> =>
  value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : {};

const strOr = (value: unknown, fallback: string): string => (typeof value === 'string' ? value : fallback);

const nonEmptyStr = (value: unknown): string | null =>
  typeof value === 'string' && value.trim().length > 0 ? value : null;

const hexOr = (value: unknown, fallback: string): string =>
  typeof value === 'string' && /^#[0-9a-fA-F]{6}$/.test(value.trim()) ? value.trim() : fallback;

const onMessageOf = (value: unknown): number | null =>
  typeof value === 'number' && Number.isInteger(value) && value >= 0 ? value : null;

const parseMessages = (items: unknown[] | undefined): MessageSpec[] =>
  (items ?? []).flatMap((raw) => {
    const m = obj(raw);
    const author = nonEmptyStr(m.author);
    const lines = Array.isArray(m.lines) ? m.lines.filter((l): l is string => typeof l === 'string') : [];
    if (!author || lines.length === 0) return [];
    return [{
      author,
      tag: strOr(m.tag, ''),
      avatarColor: hexOr(m.avatarColor, '#5865F2'),
      time: strOr(m.time, ''),
      lines,
      appearAt: Math.max(0, Math.round(finiteNumber(m.appearAt, 0))),
      typeOn: m.typeOn === true,
    }];
  });

const parseDividers = (items: unknown[] | undefined): DividerSpec[] =>
  (items ?? []).flatMap((raw) => {
    const d = obj(raw);
    const label = nonEmptyStr(d.label);
    if (!label) return [];
    return [{
      label,
      at: Math.max(0, Math.round(finiteNumber(d.at, 0))),
      jump: typeof d.jump === 'boolean' ? d.jump : null,
    }];
  });

const parseReactions = (items: unknown[] | undefined): ReactionSpec[] =>
  (items ?? []).flatMap((raw) => {
    const r = obj(raw);
    const emoji = nonEmptyStr(r.emoji);
    if (!emoji) return [];
    return [{
      emoji,
      countFrom: Math.round(finiteNumber(r.countFrom, 0)),
      countTo: Math.round(finiteNumber(r.countTo, 0)),
      startAt: Math.max(0, Math.round(finiteNumber(r.startAt, 0))),
      stepFrames: integerIn(r.stepFrames, 1, 60, 3),
      onMessage: onMessageOf(r.onMessage),
      mine: r.mine === true,
    }];
  });

const parseJoins = (items: unknown[] | undefined): JoinSpec[] =>
  (items ?? []).flatMap((raw) => {
    const j = obj(raw);
    const name = nonEmptyStr(j.name);
    if (!name) return [];
    return [{name, at: Math.max(0, Math.round(finiteNumber(j.at, 0))), time: strOr(j.time, '')}];
  });

const parseMarks = (items: unknown[] | undefined): MarkSpec[] =>
  (items ?? []).flatMap((raw) => {
    const u = obj(raw);
    const word = nonEmptyStr(u.word);
    if (!word) return [];
    return [{
      word,
      at: Math.max(0, Math.round(finiteNumber(u.at, 0))),
      color: hexOr(u.color, COLOR.orange),
      onMessage: onMessageOf(u.onMessage),
    }];
  });

const parseStrikes = (items: unknown[] | undefined): StrikeSpec[] =>
  (items ?? []).flatMap((raw) => {
    const s = obj(raw);
    const word = nonEmptyStr(s.word);
    if (!word) return [];
    return [{word, at: Math.max(0, Math.round(finiteNumber(s.at, 0))), onMessage: onMessageOf(s.onMessage)}];
  });

const DEFAULT_FRAME: FrameSpec = {width: 1280, height: 720, radius: 12, outline: '#25241F', shadow: 12};

const parseFrame = (raw: Partial<FrameSpec> | undefined): FrameSpec => {
  const f = obj(raw);
  return {
    width: integerIn(f.width, 320, 1920, DEFAULT_FRAME.width),
    height: integerIn(f.height, 180, 1080, DEFAULT_FRAME.height),
    radius: integerIn(f.radius, 0, 48, DEFAULT_FRAME.radius),
    outline: hexOr(f.outline, DEFAULT_FRAME.outline),
    shadow: integerIn(f.shadow, 0, 48, DEFAULT_FRAME.shadow),
  };
};

// Per-token decoration, keyed by "message:line:token".
type Decor = {underline?: {at: number; color: string}; strike?: {at: number}};

const findToken = (
  tokenLines: Token[][][],
  word: string,
  onMessage: number | null,
): string | null => {
  const key = wordKey(word);
  const messageIndexes = onMessage !== null ? [onMessage] : tokenLines.map((_, i) => i);
  for (const mi of messageIndexes) {
    const lines = tokenLines[mi];
    if (!lines) continue;
    for (let li = 0; li < lines.length; li += 1) {
      const tokens = lines[li];
      for (let ti = 0; ti < tokens.length; ti += 1) {
        const token = tokens[ti];
        if (token.kind !== 'mention' && wordKey(token.text) === key) return `${mi}:${li}:${ti}`;
      }
    }
  }
  return null;
};

const PixelArrow = ({color}: {color: string}): ReactElement => (
  <svg width={16} height={12} viewBox="0 0 16 12" shapeRendering="crispEdges" style={{display: 'block'}}>
    <rect x={0} y={5} width={10} height={2} fill={color} />
    <rect x={10} y={3} width={2} height={6} fill={color} />
    <rect x={12} y={4} width={2} height={4} fill={color} />
    <rect x={14} y={5} width={2} height={2} fill={color} />
  </svg>
);

// Stepped corners (6 px steps) keep the avatars on the pixel grid.
const STEPPED = 'polygon(6px 0, calc(100% - 6px) 0, 100% 6px, 100% calc(100% - 6px), calc(100% - 6px) 100%, 6px 100%, 0 calc(100% - 6px), 0 6px)';

const Avatar = ({name, color}: {name: string; color: string}): ReactElement => (
  <div
    style={{
      width: 42,
      height: 42,
      clipPath: STEPPED,
      background: color,
      color: '#FFFEFA',
      fontFamily: SANS,
      fontWeight: 700,
      fontSize: 18,
      lineHeight: '42px',
      textAlign: 'center',
      flexShrink: 0,
    }}
  >
    {name.charAt(0).toUpperCase()}
  </div>
);

const Emoji = ({value}: {value: string}): ReactElement => {
  const src = /^(https?:|data:|blob:|\/)|\.(png|svg|webp|gif)$/i.test(value) ? renderableFile(value) : null;
  if (src) {
    return <img src={src} width={18} height={18} style={{display: 'block', imageRendering: 'pixelated'}} />;
  }
  return <span style={{fontFamily: EMOJI, fontSize: 16, lineHeight: '18px'}}>{value}</span>;
};

// Split-flap date. The top half shows this tick's date and the bottom half the
// previous tick's, so each step reads as a flap turning over.
const DayText = ({text, half}: {text: string; half: 'top' | 'bottom'}): ReactElement => (
  <div
    style={{
      position: 'absolute',
      inset: 0,
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      clipPath: half === 'top' ? 'inset(0 0 50% 0)' : 'inset(50% 0 0 0)',
      fontFamily: MONO,
      fontSize: 14,
      lineHeight: '24px',
      color: PALETTE.muted,
      whiteSpace: 'nowrap',
    }}
  >
    {text}
  </div>
);

const DividerRow = ({label, flip}: {label: string; flip: {top: string; bottom: string} | null}): ReactElement => (
  <div style={{display: 'flex', alignItems: 'center', gap: 12, padding: '12px 18px'}}>
    <div style={{flex: 1, height: 1, background: PALETTE.rule}} />
    {flip ? (
      <div style={{position: 'relative', height: 24, minWidth: 210, flexShrink: 0}}>
        <DayText text={flip.top} half="top" />
        <DayText text={flip.bottom} half="bottom" />
        <div style={{position: 'absolute', left: 0, right: 0, top: 11, height: 2, background: PALETTE.chat}} />
      </div>
    ) : (
      <div style={{fontFamily: MONO, fontSize: 14, lineHeight: '24px', color: PALETTE.muted, whiteSpace: 'nowrap'}}>
        {label}
      </div>
    )}
    <div style={{flex: 1, height: 1, background: PALETTE.rule}} />
  </div>
);

type LineProps = {
  tokens: Token[];
  visible: number[];
  decor: (ti: number) => Decor | undefined;
  frame: number;
  caretAt: number | null;
};

const TokenNode = ({
  token, shown, decor, frame,
}: {token: Token; shown: number; decor: Decor | undefined; frame: number}): ReactElement => {
  const visible = token.text.slice(0, shown);
  const hidden = token.text.slice(shown);
  let body: ReactElement;
  if (token.kind === 'mention') {
    body = shown > 0 ? (
      <span style={{background: PALETTE.mention, color: PALETTE.mentionText, borderRadius: 4, padding: '0 4px', fontWeight: 600}}>
        {visible}
      </span>
    ) : (
      <span />
    );
  } else if (token.kind === 'u') {
    body = (
      <span style={{textDecoration: 'underline', textDecorationThickness: 2, textUnderlineOffset: 4}}>{visible}</span>
    );
  } else {
    body = <span>{visible}</span>;
  }
  const hiddenNode = hidden.length > 0 ? <span style={{color: 'transparent'}}>{hidden}</span> : null;
  if (!decor) {
    return (
      <>
        {body}
        {hiddenNode}
      </>
    );
  }
  const u = decor.underline;
  const s = decor.strike;
  const bars: ReactElement[] = [];
  if (u && frame >= u.at) {
    bars.push(
      <span
        key="u"
        style={{
          position: 'absolute', left: 0, bottom: -5, height: 3,
          width: `${underlineProgress(frame, u.at) * 100}%`, background: u.color,
        }}
      />,
    );
  }
  if (s && frame >= s.at) {
    bars.push(
      <span
        key="s-rust"
        style={{position: 'absolute', left: 0, right: 0, top: 11, height: 3, background: PALETTE.rust}}
      />,
    );
  }
  if (s && frame >= s.at + 1) {
    bars.push(
      <span
        key="s-orange"
        style={{position: 'absolute', left: 3, right: -3, top: 13, height: 3, background: PALETTE.orange}}
      />,
    );
  }
  return (
    <span style={{position: 'relative', display: 'inline-block'}}>
      {body}
      {hiddenNode}
      {bars}
    </span>
  );
};

const Line = ({tokens, visible, decor, frame, caretAt}: LineProps): ReactElement => {
  const nodes: ReactElement[] = [];
  tokens.forEach((token, ti) => {
    if (caretAt === ti) {
      nodes.push(<span key={`caret-${ti}`} style={{display: 'inline-block', width: 2, height: 18, background: PALETTE.text, verticalAlign: 'middle'}} />);
    }
    nodes.push(
      <TokenNode
        key={`t-${ti}`}
        token={token}
        shown={visible[ti] ?? token.text.length}
        decor={decor(ti)}
        frame={frame}
      />,
    );
  });
  if (caretAt === tokens.length) {
    nodes.push(<span key="caret-end" style={{display: 'inline-block', width: 2, height: 18, background: PALETTE.text, verticalAlign: 'middle'}} />);
  }
  return <div style={{minHeight: 24, lineHeight: '24px', whiteSpace: 'pre-wrap', color: PALETTE.body, fontFamily: SANS, fontSize: 17}}>{nodes}</div>;
};

export default function AmDiscord(props: ElementComponentProps): ReactElement | null {
  const frame = useCurrentFrame();
  const p = narrowParams<Params>(props.params);

  const server = strOr(p.server, 'OSAI');
  const channel = strOr(p.channel, '# announcements');
  const channels = Array.isArray(p.channels) && p.channels.length > 0
    ? p.channels.filter((c): c is string => typeof c === 'string')
    : ['# announcements', '# general', '# testing'];
  const messages = parseMessages(p.messages);
  const dividers = parseDividers(p.dateDividers).sort((a, b) => a.at - b.at);
  const reactions = parseReactions(p.reactions);
  const joins = parseJoins(p.joins);
  const underlines = parseMarks(p.underline);
  const strikes = parseStrikes(p.strike);
  const jumpFrames = integerIn(p.jumpFrames, 1, 60, 12);
  const typeStep = integerIn(p.typeStepFrames, 1, 12, 2);
  const badge = strOr(p.badge, 'RECONSTRUCTION · REACTIONS & JOINS ILLUSTRATIVE');
  const win = parseFrame(p.frame);

  // Tokens per message, line and token. Built once per render.
  const tokenLines: Token[][][] = messages.map((m) => m.lines.map((line) => tokenize(line)));

  // Decorations address a token. The first match wins unless onMessage is given.
  const decorMap = new Map<string, Decor>();
  for (const u of underlines) {
    const key = findToken(tokenLines, u.word, u.onMessage);
    if (key) decorMap.set(key, {...decorMap.get(key), underline: {at: u.at, color: u.color}});
  }
  for (const s of strikes) {
    const key = findToken(tokenLines, s.word, s.onMessage);
    if (key) decorMap.set(key, {...decorMap.get(key), strike: {at: s.at}});
  }

  // Reactions attach to a message: explicit onMessage, else the latest message posted by startAt.
  const reactionTarget = (r: ReactionSpec): number => {
    if (r.onMessage !== null && r.onMessage < messages.length) return r.onMessage;
    let best = 0;
    messages.forEach((m, i) => {
      if (m.appearAt <= r.startAt) best = i;
    });
    return best;
  };

  // Chat stack: messages, joins and dividers in time order, newest at the bottom.
  type Item =
    | {kind: 'message'; at: number; index: number}
    | {kind: 'join'; at: number; index: number}
    | {kind: 'divider'; at: number; index: number};
  const rank = (item: Item): number => (item.kind === 'divider' ? 0 : item.kind === 'message' ? 1 : 2);
  const items: Item[] = [
    ...messages.map((m, index) => ({kind: 'message' as const, at: m.appearAt, index})),
    ...joins.map((j, index) => ({kind: 'join' as const, at: j.at, index})),
    ...dividers.map((d, index) => ({kind: 'divider' as const, at: d.at, index})),
  ].sort((a, b) => a.at - b.at || rank(a) - rank(b));

  const visibleItems = items.filter((item) => item.at <= frame);
  const dividerFrom = (index: number): string => (index > 0 ? dividers[index - 1].label : dividers[index].label);
  const dividerJump = (index: number): boolean => {
    const d = dividers[index];
    if (d.jump !== null) return d.jump;
    return index > 0;
  };

  const renderDivider = (index: number): ReactElement | null => {
    const d = dividers[index];
    const state = dividerState(d.label, d.at, frame, dividerFrom(index), dividerJump(index), jumpFrames);
    if (state.phase === 'hidden') return null;
    if (state.phase === 'landed') return <DividerRow key={`d${index}`} label={state.text} flip={null} />;
    return <DividerRow key={`d${index}`} label="" flip={{top: state.top, bottom: state.bottom}} />;
  };

  const renderMessage = (index: number): ReactElement => {
    const m = messages[index];
    const age = frame - m.appearAt;
    const total = totalChars(m.lines);
    const shown = m.typeOn ? clamp(Math.floor(age / typeStep) + 1, 0, total) : total;
    const typing = m.typeOn && shown < total;
    // Caret sits at the first token that is not fully typed, on the line that holds it.
    let consumed = 0;
    let caretLine = -1;
    let caretToken = -1;
    if (typing) {
      for (let li = 0; li < tokenLines[index].length; li += 1) {
        const tokens = tokenLines[index][li];
        const lineChars = tokens.reduce((s, t) => s + t.text.length, 0);
        if (shown <= consumed + lineChars) {
          caretLine = li;
          let within = shown - consumed;
          caretToken = tokens.length;
          for (let ti = 0; ti < tokens.length; ti += 1) {
            if (within < tokens[ti].text.length) {
              caretToken = ti;
              break;
            }
            within -= tokens[ti].text.length;
          }
          break;
        }
        consumed += lineChars;
      }
    }
    // Per-line visible counts, derived from the global shown count.
    let left = shown;
    const lineVisible = tokenLines[index].map((tokens) => {
      const counts = revealed(tokens, left);
      left -= tokens.reduce((s, t) => s + t.text.length, 0);
      return counts;
    });
    const reacts = reactions.filter((r) => reactionTarget(r) === index && frame >= r.startAt);

    return (
      <div
        key={`m${index}`}
        style={{
          display: 'flex',
          gap: 12,
          padding: '12px 18px 0',
          transform: age === 0 ? 'translateY(6px)' : undefined,
        }}
      >
        <Avatar name={m.author} color={m.avatarColor} />
        <div style={{minWidth: 0, flex: 1}}>
          <div style={{display: 'flex', alignItems: 'center', gap: 10, lineHeight: '24px', whiteSpace: 'nowrap'}}>
            <span style={{fontFamily: SANS, fontWeight: 700, fontSize: 16, color: PALETTE.text}}>{m.author}</span>
            {m.tag ? (
              <span
                style={{
                  fontFamily: SANS, fontWeight: 700, fontSize: 11, lineHeight: '18px', letterSpacing: '0.04em',
                  background: PALETTE.blurple, color: PALETTE.blurpleInk, borderRadius: 4, padding: '0 6px',
                }}
              >
                {m.tag}
              </span>
            ) : null}
            {m.time ? (
              <span style={{fontFamily: MONO, fontSize: 13, color: PALETTE.muted}}>{m.time}</span>
            ) : null}
          </div>
          {m.lines.map((_, li) => (
            <Line
              key={li}
              tokens={tokenLines[index][li]}
              visible={lineVisible[li]}
              decor={(ti) => decorMap.get(`${index}:${li}:${ti}`)}
              frame={frame}
              caretAt={typing && caretLine === li ? caretToken : null}
            />
          ))}
          {reacts.length > 0 ? (
            <div style={{display: 'flex', flexWrap: 'wrap', gap: 6, paddingTop: 6}}>
              {reacts.map((r, ri) => {
                const count = reactionCount(r.countFrom, r.countTo, r.startAt, r.stepFrames, frame);
                const scale = stampScale(frame, r.startAt);
                return (
                  <div
                    key={ri}
                    style={{
                      display: 'flex', alignItems: 'center', gap: 6, height: 30, padding: '0 10px', borderRadius: 8,
                      background: r.mine ? 'rgba(88, 101, 242, 0.15)' : PALETTE.side,
                      border: `1px solid ${r.mine ? PALETTE.blurple : PALETTE.rule}`,
                      transform: `scale(${scale})`, transformOrigin: 'left center',
                      fontFamily: SANS, fontSize: 14, fontWeight: 600, color: PALETTE.text,
                    }}
                  >
                    <Emoji value={r.emoji} />
                    <span>{count}</span>
                  </div>
                );
              })}
            </div>
          ) : null}
        </div>
      </div>
    );
  };

  const renderJoin = (index: number): ReactElement => {
    const j = joins[index];
    const age = frame - j.at;
    return (
      <div
        key={`j${index}`}
        style={{
          display: 'flex', alignItems: 'center', gap: 12, padding: '12px 18px 0 18px',
          fontFamily: MONO, fontSize: 15, lineHeight: '24px', color: PALETTE.muted,
          transform: age === 0 ? 'translateX(-6px)' : undefined,
        }}
      >
        <div style={{width: 42, display: 'flex', justifyContent: 'center', flexShrink: 0}}>
          <PixelArrow color={PALETTE.green} />
        </div>
        <span style={{color: PALETTE.text}}>{j.name}</span>
        <span>joined the server.</span>
        {j.time ? <span style={{fontSize: 13, marginLeft: 6}}>{j.time}</span> : null}
      </div>
    );
  };

  const winLeft = (1920 - win.width) / 2;
  const winTop = (1080 - win.height) / 2;

  return (
    <div style={{position: 'absolute', inset: 0, pointerEvents: 'none', overflow: 'hidden'}}>
      <div
        style={{
          position: 'absolute',
          left: winLeft,
          top: winTop,
          width: win.width,
          height: win.height,
          borderRadius: win.radius,
          border: `2px solid ${win.outline}`,
          boxShadow: `${win.shadow}px ${win.shadow}px 0 0 ${win.outline}`,
          background: PALETTE.chat,
          overflow: 'hidden',
          display: 'flex',
          boxSizing: 'border-box',
        }}
      >
        {/* Server rail */}
        <div style={{width: 72, background: PALETTE.rail, display: 'flex', flexDirection: 'column', alignItems: 'center', paddingTop: 12, gap: 12, flexShrink: 0}}>
          <div style={{width: 48, height: 48, clipPath: STEPPED, background: PALETTE.blurple, color: PALETTE.blurpleInk, fontFamily: SANS, fontWeight: 700, fontSize: 14, lineHeight: '48px', textAlign: 'center'}}>
            {server.slice(0, 2).toUpperCase()}
          </div>
          <div style={{width: 48, height: 48, clipPath: STEPPED, background: PALETTE.chat}} />
          <div style={{width: 48, height: 48, clipPath: STEPPED, background: PALETTE.chat}} />
        </div>

        {/* Channel sidebar */}
        <div style={{width: 240, background: PALETTE.side, flexShrink: 0, display: 'flex', flexDirection: 'column'}}>
          <div style={{height: 48, lineHeight: '48px', padding: '0 18px', fontFamily: SANS, fontWeight: 700, fontSize: 16, color: PALETTE.text, borderBottom: `1px solid ${PALETTE.rail}`, whiteSpace: 'nowrap', overflow: 'hidden'}}>
            {server}
          </div>
          <div style={{padding: '18px 12px 0 12px'}}>
            <div style={{fontFamily: MONO, fontSize: 12, lineHeight: '24px', color: PALETTE.muted, padding: '0 6px', letterSpacing: '0.08em', textTransform: 'uppercase'}}>
              Text channels
            </div>
            {channels.map((name) => {
              const active = name === channel;
              return (
                <div
                  key={name}
                  style={{
                    height: 36, lineHeight: '36px', padding: '0 10px', margin: '2px 0', borderRadius: 4,
                    background: active ? PALETTE.selected : 'transparent',
                    color: active ? PALETTE.text : PALETTE.muted,
                    fontFamily: SANS, fontWeight: 500, fontSize: 16, whiteSpace: 'nowrap', overflow: 'hidden',
                  }}
                >
                  {name}
                </div>
              );
            })}
          </div>
        </div>

        {/* Main */}
        <div style={{flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column'}}>
          <div style={{height: 48, lineHeight: '48px', padding: '0 18px', fontFamily: SANS, fontWeight: 700, fontSize: 16, color: PALETTE.text, borderBottom: `1px solid ${PALETTE.rail}`, flexShrink: 0, whiteSpace: 'nowrap'}}>
            {channel}
          </div>
          <div
            style={{
              flex: 1,
              minHeight: 0,
              display: 'flex',
              flexDirection: 'column',
              justifyContent: 'flex-end',
              overflow: 'hidden',
              paddingBottom: 12,
              WebkitMaskImage: 'linear-gradient(to bottom, transparent 0, #000 60px)',
              maskImage: 'linear-gradient(to bottom, transparent 0, #000 60px)',
            }}
          >
            {visibleItems.map((item) => {
              if (item.kind === 'message') return renderMessage(item.index);
              if (item.kind === 'join') return renderJoin(item.index);
              return renderDivider(item.index);
            })}
          </div>
          <div style={{padding: '12px 18px 18px', flexShrink: 0}}>
            <div
              style={{
                height: 48, boxSizing: 'border-box', borderRadius: 8, background: PALETTE.input,
                padding: '0 16px', lineHeight: '48px', fontFamily: SANS, fontSize: 15, color: PALETTE.muted,
                whiteSpace: 'nowrap', overflow: 'hidden',
              }}
            >
              {`Message ${channel}`}
            </div>
          </div>
        </div>
      </div>

      {badge ? (
        <div
          style={{
            position: 'absolute',
            right: winLeft,
            top: winTop + win.height + 18,
            fontFamily: MONO,
            fontSize: 14,
            lineHeight: '20px',
            letterSpacing: '0.08em',
            whiteSpace: 'nowrap',
            color: COLOR.ink,
            background: COLOR.panel,
            border: `1px solid ${COLOR.ink}`,
            padding: '6px 12px',
          }}
        >
          {badge}
        </div>
      ) : null}
    </div>
  );
}
