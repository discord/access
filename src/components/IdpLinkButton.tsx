import IconButton from '@mui/material/IconButton';
import Tooltip from '@mui/material/Tooltip';
import OpenInNewIcon from '@mui/icons-material/OpenInNew';

import {idpName} from '../config/idpLink';

const moveTooltip = {modifiers: [{name: 'offset', options: {offset: [0, -10]}}]};

interface IdpLinkButtonProps {
  url: string | null;
}

export default function IdpLinkButton({url}: IdpLinkButtonProps) {
  if (url == null) {
    return null;
  }

  const button = (
    <IconButton
      aria-label={idpName ? `Open in ${idpName}` : 'Open in identity provider'}
      href={url}
      target="_blank"
      rel="noopener noreferrer">
      <OpenInNewIcon />
    </IconButton>
  );

  if (!idpName) {
    return button;
  }

  return (
    <Tooltip title={`Open in ${idpName}`} placement="top" PopperProps={moveTooltip}>
      {button}
    </Tooltip>
  );
}
