import {Box, Typography} from '@mui/material';
import ReactMarkdown from 'react-markdown';

interface MarkdownDescriptionProps {
  description: string | null | undefined;
  maxHeight?: string;
}

/**
 * Component for rendering markdown descriptions with proper styling and scrolling.
 * Used on detail pages for apps, groups, and tags.
 */
export default function MarkdownDescription({description, maxHeight = '400px'}: MarkdownDescriptionProps) {
  if (!description) {
    return null;
  }

  return (
    <Box
      sx={{
        maxHeight,
        overflowY: 'auto',
        overflowX: 'hidden',
        textAlign: 'left',
        width: 'fit-content',
        maxWidth: '100%',
        mx: 'auto',
        px: 2,
        '& a': {
          color: 'text.accent',
          textDecoration: 'none',
          '&:hover': {
            textDecoration: 'underline',
          },
        },
      }}>
      <Typography variant="body1" component="div">
        <ReactMarkdown>{description}</ReactMarkdown>
      </Typography>
    </Box>
  );
}
