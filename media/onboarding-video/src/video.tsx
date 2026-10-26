import React, {CSSProperties, ReactNode} from 'react';
import {AbsoluteFill, Img, Sequence, interpolate, spring, staticFile, useCurrentFrame} from 'remotion';
import {loadFont} from '@remotion/fonts';
import {Bubble, Buttons, HomeBack, Icon, Phone} from './phone';
import {SCENES} from './timing';

for (const [weight, file] of [['400', 'Regular'], ['600', 'SemiBold'], ['700', 'Bold']] as const) {
  loadFont({family: 'Plex', url: staticFile(`fonts/IBMPlexSans-${file}.ttf`), weight});
}

const C = {background: '#171d20', text: '#f2f3ec', dim: '#aab9b8', accent: '#ffc18a', mint: '#8bdbbc'};
const clamp = {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'} as const;
const ramp = (f: number, start: number, end: number) => interpolate(f, [start, end], [0, 1], clamp);
const pop = (f: number, delay = 0) => spring({frame: f - delay, fps: 30, config: {damping: 19, stiffness: 130, mass: 0.75}});
const appear = (f: number, delay = 0): CSSProperties => ({opacity: ramp(f, delay, delay + 8), transform: `translateY(${(1 - pop(f, delay)) * 20}px)`});
const exit = (f: number, end: number) => 1 - ramp(f, end - 6, end);

const Background: React.FC = () => {
  const f = useCurrentFrame();
  return <AbsoluteFill style={{background: C.background}}>
    <AbsoluteFill style={{background: 'radial-gradient(ellipse at 95% 26%, #62422d66, transparent 53%), radial-gradient(ellipse at 0% 80%, #284b4255, transparent 50%)'}}/>
    <AbsoluteFill style={{opacity: 0.13, backgroundImage: 'radial-gradient(#c5bba8 0.7px, transparent 0.7px)', backgroundSize: '26px 26px', transform: `translateY(${f / 35}px)`}}/>
    <div style={{position: 'absolute', width: 670, height: 670, border: '1px solid #a5bea01b', borderRadius: '50%', left: 210, top: 370, transform: `scale(${1 + Math.sin(f / 90) * 0.025})`}}/>
    <div style={{position: 'absolute', top: 42, left: 44, right: 44, display: 'flex', alignItems: 'center', gap: 12}}>
      <Img src={staticFile('holodos-avatar.png')} style={{width: 43, height: 43, borderRadius: 14, border: '1px solid #59635a'}}/>
      <div style={{fontSize: 19, letterSpacing: 2.2, fontWeight: 600}}>ХОЛОДОС</div>
      <div style={{marginLeft: 'auto', color: C.dim, fontSize: 15, letterSpacing: 1.3}}>В TELEGRAM</div>
    </div>
    <div style={{position: 'absolute', bottom: 28, left: 44, right: 44, display: 'flex', gap: 8}}>
      {SCENES.map(({from, duration}) => <div key={from} style={{height: 3, flex: 1, background: '#47514d', overflow: 'hidden', borderRadius: 4}}><div style={{height: '100%', width: `${ramp(f, from, from + duration) * 100}%`, background: C.accent}}/></div>)}
    </div>
  </AbsoluteFill>;
};

const Tag: React.FC<{children: ReactNode}> = ({children}) => <span style={{display: 'inline-flex', alignItems: 'center', gap: 8, padding: '8px 13px', borderRadius: 12, background: '#ffc18a0c', border: '1px solid #ffc18a40', color: C.accent, fontSize: 16, fontWeight: 600, letterSpacing: 1.5}}>{children}</span>;
const Title: React.FC<{number: string; label: string; icon: string; children: ReactNode}> = ({number, label, icon, children}) => <div style={{position: 'absolute', top: 120, left: 44, right: 44}}>
  <div style={{display: 'flex', gap: 14, alignItems: 'center'}}><Tag><Icon name={icon} size={19} color={C.accent}/>{number} / 04 · {label}</Tag><div style={{height: 1, flex: 1, background: '#5b685833'}}/></div>
  <div data-title style={{fontSize: 55, fontWeight: 700, letterSpacing: -2.1, lineHeight: 1.04, marginTop: 18}}>{children}</div>
</div>;
const Footer: React.FC<{f: number; from?: number; children: ReactNode}> = ({f, from = 15, children}) => <div style={{position: 'absolute', top: 1140, left: 44, right: 40, display: 'flex', gap: 14, alignItems: 'flex-start', ...appear(f, from)}}><span style={{width: 36, height: 36, borderRadius: '50%', background: '#8bdbbc12', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0}}><Icon name="check" size={22} color={C.mint}/></span><div style={{fontSize: 25, lineHeight: 1.24, fontWeight: 600}}>{children}</div></div>;
const Callout: React.FC<{f: number; from?: number; top: number; right?: boolean; small: string; children: ReactNode}> = ({f, from = 30, top, right, small, children}) => <div style={{position: 'absolute', ...(right ? {right: 24, textAlign: 'left'} : {left: 24, textAlign: 'right'}), top, width: 135, ...appear(f, from)}}><div style={{fontSize: 16, color: C.dim}}>{small}</div><div style={{fontSize: 25, lineHeight: 1.13, color: C.accent, fontWeight: 600, marginTop: 9}}>{children}</div><div style={{width: 26, height: 1, background: '#bda083', marginTop: 13, ...(right ? {} : {marginLeft: 'auto'})}}/></div>;
const Tap: React.FC<{f: number; at: number; x: number; y: number}> = ({f, at, x, y}) => {
  const p = ramp(f, at, at + 16);
  return <div style={{position: 'absolute', left: x - 21, top: y - 21, width: 42, height: 42, border: '2px solid #a9daff', borderRadius: '50%', background: '#d3efff40', opacity: f < at ? 0 : 1 - p, transform: `scale(${0.6 + p * 1.3})`}}/>;
};
const MiniIcon: React.FC<{name: string}> = ({name}) => <Img src={staticFile(`${name}.png`)} style={{width: 23, height: 23, objectFit: 'contain'}}/>;
const Character: React.FC<{f: number; size: number}> = ({f, size}) => <Img src={staticFile('holodos-avatar.png')} style={{width: size, height: size, objectFit: 'contain', maskImage: 'radial-gradient(ellipse 52% 52% at 50% 50%, #000 76%, transparent 100%)', transform: `translateY(${Math.sin(f / 21) * 5}px) rotate(${Math.sin(f / 44) * 1.1}deg)`}}/>;

const Intro: React.FC = () => {
  const f = useCurrentFrame();
  return <AbsoluteFill style={{opacity: exit(f, 84)}}>
    <div style={{position: 'absolute', left: 44, top: 148, ...appear(f)}}><Tag>ТВОЙ ПОМОЩНИК НА КУХНЕ</Tag><div style={{fontSize: 83, fontWeight: 700, letterSpacing: -3.3, lineHeight: 1.02, marginTop: 25}}>Что сегодня<br/><span style={{color: C.accent}}>приготовить?</span></div></div>
    <div style={{position: 'absolute', top: 400, left: 80, ...appear(f, 3)}}><Character f={f} size={560}/></div>
    {[
      {text: 'Из продуктов', icon: 'fridge', left: 43, top: 490, delay: 10, rot: -7},
      {text: 'Рецепты', icon: 'dish', left: 470, top: 650, delay: 15, rot: 6},
      {text: 'План питания', icon: 'plan', left: 44, top: 856, delay: 20, rot: -4},
      {text: 'Покупки', icon: 'shopping', left: 463, top: 924, delay: 25, rot: 5},
    ].map(chip => <div key={chip.text} style={{position: 'absolute', left: chip.left, top: chip.top, background: '#344039', border: '1.5px solid #779b7d', boxShadow: '0 7px 0 #101a17', borderRadius: 20, padding: '13px 15px', display: 'flex', gap: 10, alignItems: 'center', fontSize: 24, fontWeight: 600, opacity: ramp(f, chip.delay, chip.delay + 8), transform: `translateY(${(1 - pop(f, chip.delay)) * 24}px) rotate(${chip.rot}deg)`}}><MiniIcon name={chip.icon}/>{chip.text}</div>)}
    <div style={{position: 'absolute', left: 44, top: 1070, fontSize: 26, fontWeight: 600, ...appear(f, 20)}}>Открой холодильник. Остальное — проще.</div>
    <div style={{position: 'absolute', left: 44, top: 1123, fontSize: 23, color: C.dim, ...appear(f, 23)}}>Смотри, как это работает ↓</div>
  </AbsoluteFill>;
};

const Fridge: React.FC = () => {
  const f = useCurrentFrame();
  const scroll = ramp(f, 117, 135) * 315;
  return <AbsoluteFill style={{opacity: exit(f, 216)}}>
    <Title number="01" label="ТВОИ ПРОДУКТЫ" icon="photo">Пришли фото.<br/><span style={{color: C.accent}}>Холодос разберётся.</span></Title>
    <Phone typing={f > 20 && f < 48}>
      <div style={{position: 'absolute', inset: 0, transform: `translateY(${-scroll}px)`}}>
        <Bubble top={44} right width={285} photo style={appear(f, 5)}><Img src={staticFile('fridge-input.png')} style={{width: '100%', height: 183, objectFit: 'cover', borderRadius: 11, display: 'block'}}/><div style={{fontSize: 20, padding: '7px 6px 0'}}>Вот что есть</div></Bubble>
        <Bubble top={303} style={appear(f, 46)} fontSize={23}><b>Твои продукты</b>{'\n• Яйца\n• Помидоры\n• Молоко\n• Твёрдый сыр\n• Зелень'}<div style={{fontSize: 17, color: '#d2dce4', marginTop: 13}}>Что-то поправить? Напиши, отправь голосовое или фотографию.</div></Bubble>
        <Bubble top={620} width={303} right fontSize={22} style={appear(f, 137)}>{'Без мяса, до 30 минут.\nМасло, соль и специи есть.'}</Bubble>
        <Bubble top={738} fontSize={22} style={appear(f, 167)}>Учту: без мяса, до 30 минут.</Bubble>
        <Buttons top={810} rows={[[<React.Fragment key="choose"><MiniIcon name="dish"/>Подобрать блюда</React.Fragment>], HomeBack]} style={appear(f, 167)}/>
        <Tap f={f} at={193} x={175} y={830}/>
      </div>
    </Phone>
    <Callout f={f} from={52} top={692} small="Список" >Уже<br/>в чате</Callout>
    <Footer f={f} from={55}>Фото, текст или голос —<br/><span style={{color: C.mint}}>как тебе удобнее.</span></Footer>
  </AbsoluteFill>;
};

const Recipe: React.FC = () => {
  const f = useCurrentFrame();
  const opened = ramp(f, 83, 98);
  return <AbsoluteFill style={{opacity: exit(f, 204)}}>
    <Title number="02" label="РЕЦЕПТЫ" icon="dish">Из того, что есть.<br/><span style={{color: C.accent}}>Сразу к рецепту.</span></Title>
    <Phone>
      <div style={{position: 'absolute', inset: 0, transform: `translateY(${-opened * 410 - ramp(f, 145, 168) * 190}px)`}}>
        <Bubble top={45} photo style={appear(f, 6)}><Img src={staticFile('omelette.jpg')} style={{width: '100%', height: 233, objectFit: 'cover', borderRadius: 11, display: 'block'}}/><div style={{padding: '9px 7px 0', fontSize: 23}}>Омлет с помидорами<br/>и сыром</div></Bubble>
        <Buttons top={397} rows={[[<React.Fragment key="recipe"><MiniIcon name="dish"/>Омлет с помидорами и сыром</React.Fragment>], HomeBack]} style={{...appear(f, 15), opacity: ramp(f, 15, 23) * (1 - opened)}}/>
        <Tap f={f} at={72} x={180} y={419}/>
        <Bubble top={462} style={{opacity: opened}} fontSize={21}>
          <b>Омлет с помидорами<br/>и сыром</b><div style={{color: '#91caf3', fontSize: 18, marginTop: 7}}>Источник · 1000.menu ↗</div>
          <div style={{marginTop: 16}}><b>Ингредиенты:</b>{'\n• Яйца — 3 шт.\n• Помидоры — 2 шт.\n• Молоко — 100 мл\n• Твёрдый сыр — 50 г\n• Масло — 20 г\n• Зелень, соль и специи —\n  по вкусу'}</div>
          <div style={{marginTop: 17}}><b>Как приготовить:</b>{'\n1. Нарежь помидоры, натри сыр.\n2. Смешай яйца, молоко, соль и специи.\n3. Вылей смесь на сковороду с маслом. Добавь помидоры и сыр.\n4. Готовь под крышкой на слабом огне 4–6 минут. Посыпь зеленью.'}</div>
          <div style={{marginTop: 15}}>Время: 15 мин · Порции: 2</div>
        </Bubble>
      </div>
    </Phone>
    <Callout f={f} from={22} top={665} small="Этот рецепт">15 минут</Callout>
    <Callout f={f} from={104} top={867} right small={f < 150 ? 'Источник' : 'Рецепт'}>{f < 150 ? <>Можно<br/>открыть</> : <>Всё<br/>по шагам</>}</Callout>
    <Footer f={f} from={28}>Фото, состав и шаги.<br/><span style={{color: C.mint}}>Со ссылкой на настоящий рецепт.</span></Footer>
  </AbsoluteFill>;
};

const Plan: React.FC = () => {
  const f = useCurrentFrame();
  const ready = ramp(f, 55, 66);
  return <AbsoluteFill style={{opacity: exit(f, 156)}}>
    <Title number="03" label="ПЛАН ПИТАНИЯ" icon="calendar">Меньше «что поесть?».<br/><span style={{color: C.accent}}>Больше готового плана.</span></Title>
    <Phone typing={f > 48 && f < 66}>
      <div style={{position: 'absolute', inset: 0, opacity: 1 - ready}}>
        <Bubble top={48} fontSize={22}><b>Твой план питания</b>{'\n\n2 человека · 2 приёма пищи\nБюджет: 100 € на неделю\nРегион: Испания\nБез мяса\n\nНа какой срок составить меню?'}</Bubble>
        <Buttons top={354} rows={[[<>На день</>, <>На неделю</>], ['Изменить профиль'], HomeBack]}/>
        <Tap f={f} at={43} x={93} y={374}/>
      </div>
      <div style={{position: 'absolute', inset: 0, opacity: ready, transform: `translateY(${(1 - ready) * 15}px)`}}>
        <Bubble top={48} fontSize={22}><b>День 1 из 1 · на 2 чел.</b><div style={{fontSize: 17, marginTop: 11, color: '#d2dce4'}}>Нажми на блюдо: открыть рецепт или выбрать другое.</div><div style={{marginTop: 22}}><b>Завтрак</b>{'\nОмлет с помидорами и сыром'}</div><div style={{marginTop: 20}}><b>Ужин</b>{'\nПаста с томатами и шпинатом'}</div></Bubble>
        <Buttons top={369} rows={[[<>Завтрак: омлет</>], [<>Ужин: паста со шпинатом</>], [<React.Fragment key="shopping"><MiniIcon name="shopping"/>Что докупить</React.Fragment>], HomeBack]}/>
      </div>
    </Phone>
    <Callout f={f} from={70} top={662} small="Под тебя">Вкусы<br/>и бюджет</Callout>
    <Footer f={f} from={18}>Меню на день или на неделю.<br/><span style={{color: C.mint}}>Любое блюдо можно заменить.</span></Footer>
  </AbsoluteFill>;
};

const Shopping: React.FC = () => {
  const f = useCurrentFrame();
  const scrolled = ramp(f, 75, 104) * 90;
  return <AbsoluteFill style={{opacity: exit(f, 135)}}>
    <Title number="04" label="ПОКУПКИ" icon="shopping">В магазин —<br/><span style={{color: C.accent}}>с готовым списком.</span></Title>
    <Phone>
      <Bubble top={48 - scrolled} fontSize={23} style={appear(f, 4)}><b>Что докупить</b><div style={{fontSize: 17, marginTop: 12, marginBottom: 21, color: '#d2dce4'}}>Для выбранного меню на двоих</div>
        {'• Макароны\n\n• Шпинат\n\n• Сливочный сыр\n\n• Томаты в собственном соку\n\n• Пармезан\n\n• Лук и чеснок\n\n• Томатная паста\n\n• Сушёные травы'}
      </Bubble>
    </Phone>
    <Callout f={f} from={20} top={654} small="Учитывает">Что уже<br/>есть дома</Callout>
    <Footer f={f} from={10}>Покупки под выбранные блюда.<br/><span style={{color: C.mint}}>С учётом твоих запасов.</span></Footer>
  </AbsoluteFill>;
};

const Outro: React.FC = () => {
  const f = useCurrentFrame();
  return <AbsoluteFill>
    <div style={{position: 'absolute', left: 44, top: 148, ...appear(f)}}><Tag>ОТ ПРОДУКТОВ — К УЖИНУ</Tag><div style={{fontSize: 78, fontWeight: 700, letterSpacing: -3, lineHeight: 1.04, marginTop: 26}}>Холодос.<br/><span style={{color: C.accent}}>Пора готовить.</span></div></div>
    <div style={{position: 'absolute', top: 401, left: 115, ...appear(f, 3)}}><Character f={f + 30} size={490}/></div>
    <div style={{position: 'absolute', top: 904, left: 44, right: 44, display: 'flex', gap: 8, ...appear(f, 10)}}>{[['fridge', 'Продукты'], ['dish', 'Рецепты'], ['plan', 'Меню'], ['shopping', 'Покупки']].map(([icon, name]) => <div key={name} style={{flex: 1, border: '1px solid #69857266', borderRadius: 17, padding: '12px 4px', background: '#34403988', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 9, fontSize: 20}}><MiniIcon name={icon}/>{name}</div>)}</div>
    <div style={{position: 'absolute', top: 1035, left: 44, right: 44, padding: '24px 20px', borderRadius: 25, background: C.accent, boxShadow: '0 7px 0 #997d5b', color: '#292e26', fontSize: 31, fontWeight: 700, display: 'flex', gap: 22, justifyContent: 'center', alignItems: 'center', ...appear(f, 18)}}>Отправь фото холодильника<Icon name="arrow" size={30} color="#292e26"/></div>
    <div style={{position: 'absolute', top: 1160, left: 44, right: 44, textAlign: 'center', fontSize: 24, color: C.dim, ...appear(f, 20)}}>@holodos_recept_bot</div>
  </AbsoluteFill>;
};

const components = {intro: Intro, fridge: Fridge, recipe: Recipe, plan: Plan, shopping: Shopping, outro: Outro};
export const HolodosIntro: React.FC = () => <AbsoluteFill style={{fontFamily: 'Plex, sans-serif', color: C.text}}><Background/>{SCENES.map(scene => {
  const Scene = components[scene.id];
  return <Sequence key={scene.id} from={scene.from} durationInFrames={scene.duration}><Scene/></Sequence>;
})}</AbsoluteFill>;
