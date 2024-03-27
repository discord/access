import Avatar from '@mui/material/Avatar';
import Typography from '@mui/material/Typography';

function stringToColor(string: string) {
  let hash = 0;
  let i;

  /* eslint-disable no-bitwise */
  for (i = 0; i < string.length; i += 1) {
    hash = string.charCodeAt(i) + ((hash << 5) - hash);
  }

  let color = '#';

  for (i = 0; i < 3; i += 1) {
    const value = (hash >> (i * 8)) & 0xff;
    color += `00${value.toString(16)}`.slice(-2);
  }
  /* eslint-enable no-bitwise */

  return color;
}

interface UserAvatarProps {
  name: string;
  size?: number;
  variant?: string;
}

export default function UserAvatar(props: UserAvatarProps) {
  const splitName = props.name.split(' ');

  return (
    <Avatar
      alt={props.name}
      sx={{
        bgcolor: stringToColor(props.name),
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
