import * as React from 'react';

import Autocomplete from '@mui/material/Autocomplete';
import Chip from '@mui/material/Chip';
import TextField from '@mui/material/TextField';

import {AppDetail, TagSummary} from '../../api/apiSchemas';

/** The tags a group picks up from the one app it is created under. */
export interface InheritedTags {
  /** That app, named on each chip so the tags read as its rather than the form's. */
  appName: string;
  tags: TagSummary[];
}

/**
 * The tags `CreateGroup` copies from an app onto a group created under it, or
 * null when it would copy none.
 *
 * A tag in `chosenTagIds` is left out. It was asked for directly, so it is
 * submitted as a tag of the group's own and stays the requester's to remove.
 *
 * @param app The app the group would be created under; undefined while that is
 *   still loading, or when the group is not an app group and inherits nothing.
 * @param chosenTagIds Ids of the tags chosen on the form.
 */
export function inheritedAppTags(app: AppDetail | undefined, chosenTagIds: readonly string[]): InheritedTags | null {
  if (app == null) return null;
  const chosen = new Set(chosenTagIds);
  const tags = (app.active_app_tags ?? [])
    .map((mapping) => mapping.active_tag)
    .filter((tag): tag is TagSummary => tag != null && !chosen.has(tag.id));
  return tags.length > 0 ? {appName: app.name, tags} : null;
}

/** An entry of the field's own list, whether chosen here or supplied by the app. */
interface FieldTag {
  id: string;
  label: string;
  /** True for a tag the app supplies, which is the form's to show, not to edit. */
  inherited: boolean;
}

interface TagsFieldProps<T extends {id: string; name: string}> {
  /** Tags the current search offers. */
  options: T[];
  /** Tags chosen here, which are the ones the form submits. */
  selected: T[];
  /** Tags the app contributes on top of those. */
  inherited: InheritedTags | null;
  onSelectedChange: (tags: T[]) => void;
  onSearchChange: (query: string) => void;
}

/**
 * The Tags field of a group request, listing what the new group will carry.
 *
 * Inherited tags sit in the same list as chosen ones because the group ends up
 * holding both, and a field showing only half of that understates the
 * constraints the group is about to come under. They are shown first, keep no
 * delete control, and cannot be picked from the menu: they come with the app,
 * so neither requester nor approver adds or removes them here.
 *
 * Only the chosen tags reach `onSelectedChange`, and so only they are
 * submitted. Submitting an inherited one would write a second tag map tied to
 * the group rather than the app, leaving the tag on the group after it is taken
 * off the app.
 */
export default function TagsField<T extends {id: string; name: string}>({
  options,
  selected,
  inherited,
  onSelectedChange,
  onSearchChange,
}: TagsFieldProps<T>) {
  const inheritedIds = React.useMemo(() => new Set(inherited?.tags.map((tag) => tag.id)), [inherited]);

  // The caller's own tags by id, so a change event can be answered in the type
  // it handed over. Carries the selection as well as the search results, which
  // a narrowed query no longer lists.
  const chosenById = React.useMemo(() => {
    const index = new Map<string, T>();
    for (const tag of [...options, ...selected]) {
      index.set(tag.id, tag);
    }
    return index;
  }, [options, selected]);

  const value = React.useMemo<FieldTag[]>(() => {
    const fromApp = inherited
      ? inherited.tags.map((tag) => ({id: tag.id, label: `${tag.name} (${inherited.appName})`, inherited: true}))
      : [];
    return [...fromApp, ...selected.map((tag) => ({id: tag.id, label: tag.name, inherited: false}))];
  }, [inherited, selected]);

  const fieldOptions = React.useMemo<FieldTag[]>(
    () => options.map((tag) => ({id: tag.id, label: tag.name, inherited: inheritedIds.has(tag.id)})),
    [options, inheritedIds],
  );

  return (
    <Autocomplete
      multiple
      options={fieldOptions}
      value={value}
      getOptionLabel={(option) => option.label}
      isOptionEqualToValue={(option, selectedOption) => option.id === selectedOption.id}
      getOptionDisabled={(option) => option.inherited}
      onInputChange={(_event, query) => onSearchChange(query)}
      // Backspace on an empty input deletes the last chip, which is why this
      // drops the inherited ones rather than trusting them to stay: whatever
      // comes back, the selection is what is left after they are taken out, and
      // the field re-renders them.
      onChange={(_event, newValue) =>
        onSelectedChange(
          newValue
            .filter((tag) => !inheritedIds.has(tag.id))
            .map((tag) => chosenById.get(tag.id))
            .filter((tag): tag is T => tag != null),
        )
      }
      renderTags={(tags, getTagProps) =>
        tags.map((tag, index) => {
          const {key, onDelete, ...chipProps} = getTagProps({index});
          return (
            <Chip
              key={key}
              variant="outlined"
              label={tag.label}
              {...chipProps}
              onDelete={tag.inherited ? undefined : onDelete}
            />
          );
        })
      }
      renderInput={(params) => <TextField {...params} label="Tags" placeholder="Tags" />}
    />
  );
}
