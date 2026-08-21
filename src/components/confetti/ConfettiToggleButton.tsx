import * as React from 'react';
import CelebrationIcon from '@mui/icons-material/Celebration';
import ToggleButton from '@mui/material/ToggleButton';
import Tooltip from '@mui/material/Tooltip';
import Zoom from '@mui/material/Zoom';

import {useConfetti} from './ConfettiContext';

export default function ConfettiToggleButton() {
  const {unlocked, enabled, reducedMotion, setEnabled} = useConfetti();

  if (!unlocked) {
    return null;
  }

  const label = reducedMotion
    ? "Confetti cannon (held back by your system's reduced motion setting)"
    : enabled
      ? 'Stop the confetti'
      : 'Confetti cannon: confetti as you type';

  const handleClick = (event: React.MouseEvent<HTMLElement>) => {
    // Launch the celebratory burst from the button the user just hit.
    const rect = event.currentTarget.getBoundingClientRect();
    setEnabled(!enabled, {x: rect.left + rect.width / 2, y: rect.top + rect.height / 2});
  };

  return (
    // Zoom has to sit outside Tooltip: it hands the transition style down to
    // its child, and only Tooltip forwards that on to the button.
    <Zoom in>
      <Tooltip title={label}>
        <ToggleButton value="confetti" size="small" selected={enabled} onClick={handleClick} aria-label={label}>
          <CelebrationIcon />
        </ToggleButton>
      </Tooltip>
    </Zoom>
  );
}
