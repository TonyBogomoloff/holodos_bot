import React from 'react';
import {Composition, registerRoot} from 'remotion';
import {HolodosIntro} from './video';
import {DURATION_IN_FRAMES} from './timing';

const Root: React.FC = () => <Composition id="HolodosIntro" component={HolodosIntro}
  width={720} height={1280} fps={30} durationInFrames={DURATION_IN_FRAMES}/>;

registerRoot(Root);
