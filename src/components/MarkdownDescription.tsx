import {Box, Typography} from '@mui/material';
import ReactMarkdown from 'react-markdown';

interface MarkdownDescriptionProps {
  description: string | null | undefined;
}

/**
 * Component for rendering markdown descriptions with proper styling.
 * Used on detail pages for apps, groups, and tags.
 */
export default function MarkdownDescription({description}: MarkdownDescriptionProps) {
  if (!description) {
    return null;
  }

  return (
    <Box
      sx={{
        textAlign: 'left',
        width: 'fit-content',
        maxWidth: '100%',
        mx: 'auto',
        px: 2,
        // Styling for links, using a color with better contrast in dark mode.
        '& a': {
          color: 'text.accent',
          textDecoration: 'none',
          '&:hover': {
            textDecoration: 'underline',
          },
        },
      }}>
      <Typography variant="body1" component="div">
        <ReactMarkdown
          allowedElements={[
            'p',
            'strong',
            'em',
            'code',
            'pre',
            'del',
            'a',
            'ul',
            'ol',
            'li',
            'h1',
            'h2',
            'h3',
            'h4',
            'h5',
            'h6',
            'blockquote',
            'hr',
            'br',
          ]}>
          {description}
        </ReactMarkdown>
      </Typography>
    </Box>
  );
}
