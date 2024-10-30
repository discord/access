import {Avatar, ButtonBase, Typography} from '@mui/material';
import {ReactNode} from 'react';

interface AvatarButtonProps {
  icon: ReactNode;
  text?: string;
  strikethrough?: boolean;
  onClick?: () => void;
}

export default function AvatarButton({icon, text, strikethrough, onClick}: AvatarButtonProps) {
  return (
    <ButtonBase
      sx={{display: 'flex', flexDirection: 'column', gap: 0.5, p: 0.5, borderRadius: 2, minWidth: 80}}
      onClick={onClick}>
      <Avatar sx={{bgcolor: 'primary.main'}}>{icon}</Avatar>
      {text && (
        <Typography variant="body2" sx={{...(strikethrough && {textDecoration: 'line-through'})}}>
          {text}
        </Typography>
      )}
    </ButtonBase>
  );
}
