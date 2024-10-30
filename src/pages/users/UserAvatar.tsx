import Avatar from '@mui/material/Avatar';
import Typography from '@mui/material/Typography';

function stringToColor(string: string) {
  const hue = string.split('').reduce((acc, curr) => curr.charCodeAt(0) + acc, 0) % 360;
  return `hsl(${hue}, 65%, 65%)`;
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
