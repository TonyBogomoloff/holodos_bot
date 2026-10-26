import React, {CSSProperties, ReactNode} from 'react';
import {Img, staticFile} from 'remotion';
import {loadFont} from '@remotion/fonts';

loadFont({family: 'Android', url: staticFile('fonts/Roboto-Variable.ttf'), weight: '100 900'});

const ui = {bar: '#202d3b', background: '#0f1b26', incoming: '#233343', outgoing: '#31597d', text: '#f3f6f8', blue: '#91caf3', dim: '#99acbd'};

export const Icon: React.FC<{name: string; size?: number; color?: string}> = ({name, size = 23, color = ui.text}) => {
  const paths: Record<string, ReactNode> = {
    back: <path d="M20 12H4m7-7-7 7 7 7"/>,
    more: <><circle cx="12" cy="5" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="12" cy="19" r="1"/></>,
    clip: <path d="m8 15 7-7c4-4 8 1 4 5l-9 9c-5 4-11-2-7-6L14 5c2-2 5 1 3 3L7 18c-1 1-2-1-1-2l8-8"/>,
    smile: <><circle cx="12" cy="12" r="9"/><path d="M7.5 14.5q4.5 6 9 0M8 8h.1M16 8h.1"/></>,
    mic: <><rect x="9" y="3" width="6" height="12" rx="3"/><path d="M5 10v2a7 7 0 0 0 14 0v-2M12 19v3M9 22h6"/></>,
    checks: <path d="m2 12 4 4L16 6M10 14l2 2L22 6"/>,
    arrow: <path d="M4 12h16m-6-6 6 6-6 6"/>,
    check: <path d="m4 12 5 5L20 6"/>,
    home: <><path d="m3 10 9-7 9 7M6 9v12h12V9M10 21v-7h4v7"/></>,
    photo: <><rect x="3" y="4" width="18" height="16" rx="3"/><circle cx="8" cy="9" r="2"/><path d="m4 17 5-5 4 4 3-4 5 6"/></>,
    dish: <><path d="M3 18h18M5 15a7 7 0 0 1 14 0H5ZM12 8V5M10 5h4"/></>,
    calendar: <><rect x="3" y="5" width="18" height="16" rx="3"/><path d="M7 3v5M17 3v5M3 11h18M8 15h2M14 15h2"/></>,
    shopping: <><path d="M4 8h16l-2 13H6L4 8ZM8 8V6a4 4 0 0 1 8 0v2"/></>,
    clock: <><circle cx="12" cy="12" r="9"/><path d="M12 6v6l4 2"/></>,
  };
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" style={{flexShrink: 0}}>{paths[name]}</svg>;
};

const Wallpaper: React.FC = () => <svg width="100%" height="100%" style={{position: 'absolute', inset: 0, opacity: 0.16}}>
  <defs><pattern id="wallpaper" width="140" height="155" patternUnits="userSpaceOnUse"><g fill="none" stroke="#476273" strokeWidth="1.3">
    <path d="M25 27c-8-13-23-1-14 10l14 12 15-12c10-12-8-24-15-10Z"/>
    <path d="m98 10 29 10-21 20-3-14-13-6 37 0M54 80h39v27H72l-12 9 2-9h-8zM62 88h22M62 96h17"/>
    <path d="m116 114 4 9 10 1-8 7 2 10-8-5-9 5 2-10-7-7 10-1Z"/><circle cx="22" cy="116" r="7"/>
  </g></pattern></defs><rect width="100%" height="100%" fill="url(#wallpaper)"/>
</svg>;

// Same 360 × 800 (9:20) phone viewport as the approved Your Secret Agent V4.
export const Phone: React.FC<{children: ReactNode; style?: CSSProperties; typing?: boolean}> = ({children, style, typing}) => <div data-phone style={{position: 'absolute', top: 310, left: 180, width: 360, height: 800, borderRadius: 31, overflow: 'hidden', outline: '2px solid #53636e', boxShadow: '0 16px 35px #0007', fontFamily: 'Android, sans-serif', color: ui.text, background: ui.background, ...style}}>
  <div style={{height: 25, background: ui.bar, display: 'flex', alignItems: 'center', padding: '0 19px', fontSize: 12, fontWeight: 500, gap: 5}}>
    <span>18:42</span><span style={{marginLeft: 'auto', fontSize: 10}}>4G</span>
    <svg width="15" height="13" viewBox="0 0 20 18"><path d="M2 16h3v-4H2zm5 0h3V8H7zm5 0h3V4h-3zm5 0h2V1h-2z" fill={ui.text}/></svg>
    <svg width="21" height="14" viewBox="0 0 27 18"><rect x="1" y="4" width="21" height="11" rx="2" fill="none" stroke={ui.text} strokeWidth="1.3"/><path d="M24 7v5" stroke={ui.text} strokeWidth="2"/><rect x="3" y="6" width="15" height="7" rx="1" fill={ui.text}/></svg>
  </div>
  <div style={{height: 59, background: ui.bar, display: 'flex', alignItems: 'center', padding: '0 12px', gap: 10, boxShadow: '0 2px 4px #0003', position: 'relative', zIndex: 1}}>
    <Icon name="back"/><Img src={staticFile('holodos-avatar.png')} style={{width: 35, height: 35, borderRadius: '50%'}}/>
    <div><div style={{fontSize: 20, fontWeight: 500}}>Холодос</div><div style={{fontSize: 13, color: typing ? ui.blue : ui.dim, marginTop: 2}}>{typing ? 'печатает…' : 'бот'}</div></div>
    <div style={{marginLeft: 'auto'}}><Icon name="more"/></div>
  </div>
  <div data-chat-viewport style={{position: 'absolute', top: 84, bottom: 67, left: 0, right: 0, overflow: 'hidden'}}>
    <Wallpaper/>
    <div style={{position: 'absolute', top: 11, left: 0, right: 0, textAlign: 'center'}}><span style={{fontSize: 12, background: '#294355c9', padding: '4px 10px', borderRadius: 18, color: '#d2e0e9'}}>Сегодня</span></div>
    {children}
  </div>
  <div style={{position: 'absolute', bottom: 17, left: 7, right: 7, height: 43, display: 'flex', gap: 6, alignItems: 'center'}}>
    <div style={{height: 43, background: ui.bar, borderRadius: 24, padding: '0 11px', display: 'flex', alignItems: 'center', gap: 10, flex: 1}}>
      <Icon name="smile" color={ui.dim}/><span style={{fontSize: 17, color: ui.dim}}>Сообщение</span><span style={{marginLeft: 'auto', display: 'flex'}}><Icon name="clip" size={22} color={ui.dim}/></span>
    </div><div style={{width: 43, height: 43, background: '#578fbd', borderRadius: '50%', display: 'flex', alignItems: 'center', justifyContent: 'center'}}><Icon name="mic" size={24}/></div>
  </div>
  <div style={{position: 'absolute', bottom: 5, left: 131, width: 98, height: 3, background: '#becad1', borderRadius: 4}}/>
</div>;

export const Bubble: React.FC<{children: ReactNode; top: number; right?: boolean; width?: number; style?: CSSProperties; fontSize?: number; photo?: boolean}> = ({children, top, right, width = 325, style, fontSize = 22, photo}) => <div data-bubble style={{position: 'absolute', top, ...(right ? {right: 11} : {left: 11}), width, boxSizing: 'border-box', padding: photo ? '4px 4px 5px' : '10px 12px 7px', borderRadius: right ? '15px 15px 4px 15px' : '15px 15px 15px 4px', background: right ? ui.outgoing : ui.incoming, fontSize, lineHeight: 1.25, ...style}}>
  <div style={{whiteSpace: 'pre-wrap', overflowWrap: 'break-word'}}>{children}</div>
  <div style={{display: 'flex', justifyContent: 'flex-end', alignItems: 'center', gap: 4, fontSize: 11, color: right ? '#b3cce0' : '#99aebf', marginTop: 4, height: 14, paddingRight: photo ? 5 : 0}}>18:42{right && <Icon name="checks" size={16} color="#82c5f4"/>}</div>
</div>;

export const Buttons: React.FC<{top: number; rows: ReactNode[][]; width?: number; style?: CSSProperties}> = ({top, rows, width = 325, style}) => <div style={{position: 'absolute', top, left: 11, width, display: 'grid', gap: 4, ...style}}>{rows.map((row, i) => <div key={i} style={{display: 'flex', gap: 4}}>{row.map((label, j) => <div data-inline-button key={j} style={{flex: 1, minWidth: 0, background: '#29485dbf', borderRadius: 7, minHeight: 40, padding: '6px 9px', boxSizing: 'border-box', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 7, textAlign: 'center', fontSize: 17, fontWeight: 500, lineHeight: 1.15}}>{label}</div>)}</div>)}</div>;

export const HomeBack = [<Icon key="back" name="back" size={20}/>, <Icon key="home" name="home" size={20}/>];
