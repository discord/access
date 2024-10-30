import {PaletteMode, useTheme} from '@mui/material';
import Avatar from '@mui/material/Avatar';
import Typography from '@mui/material/Typography';

function stringToColor(string: string, mode: PaletteMode) {
  const hue = string.split('').reduce((acc, curr) => curr.charCodeAt(0) + acc, 0) % 360;
  if (mode === 'dark') {
    return `hsl(${hue}, 65%, 70%)`;
  } else {
    return `hsl(${hue}, 65%, 55%)`;
  }
}

interface UserAvatarProps {
  name: string;
  size?: number;
  variant?: string;
}

export default function UserAvatar(props: UserAvatarProps) {
  const {
    palette: {mode},
  } = useTheme();
  const splitName = props.name.split(' ');

  return (
    <Avatar
      alt={props.name}
      sx={{
        bgcolor: stringToColor(props.name, mode),
        width: props.size ?? 24,
        height: props.size ?? 24,
      }}
      variant={'rounded' as any}>
      <Typography variant={(props.variant ?? 'body1') as any}>
        {splitName.length > 1 ? splitName[0][0] + splitName[1][0] : splitName[0][0]}
      </Typography>
    </Avatar>
  );
}
