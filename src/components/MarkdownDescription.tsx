import {Box, Typography} from '@mui/material';
import type {SxProps, Theme} from '@mui/material';
import ReactMarkdown from 'react-markdown';

interface MarkdownDescriptionProps {
  description: string | null | undefined;
  inline?: boolean;
  sx?: SxProps<Theme>;
}

const FULL_ELEMENTS = [
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
];

const INLINE_ELEMENTS = ['strong', 'em', 'code', 'del', 'br'];

/**
 * Renders markdown descriptions. Default mode is for detail-page heroes
 * (centered, full block-level features). `inline` mode renders inside table
 * cells whose row is wrapped in <a>: single-line CSS clamp, inline-only
 * formatting, and links unwrapped to plain text to avoid nested anchors.
 */
export default function MarkdownDescription({description, inline, sx}: MarkdownDescriptionProps) {
  if (!description) {
    return null;
  }

  if (inline) {
    return (
      <Box
        sx={{
          display: '-webkit-box',
          WebkitLineClamp: 1,
          WebkitBoxOrient: 'vertical',
          overflow: 'hidden',
          ...sx,
        }}>
        <ReactMarkdown allowedElements={INLINE_ELEMENTS} unwrapDisallowed>
          {description}
        </ReactMarkdown>
      </Box>
    );
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
        ...sx,
      }}>
      <Typography variant="body1" component="div">
        <ReactMarkdown allowedElements={FULL_ELEMENTS}>{description}</ReactMarkdown>
      </Typography>
    </Box>
  );
}
